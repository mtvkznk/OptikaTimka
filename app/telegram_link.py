"""One-time administrator linking, confirmed entirely in Telegram; never website login."""

import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete
from sqlmodel import Session, select

from app.admin_audit import add_admin_audit
from app.booking import lock_booking_writes
from app.config import settings
from app.database import get_session
from app.models import (
    Customer,
    CustomerSession,
    TelegramAccount,
    TelegramLink,
    TelegramView,
    utc_now,
)
from app.security import (
    SESSION_COOKIE_NAME,
    cookie_name,
    get_customer_by_session,
    hash_secret,
    hash_session_token,
    is_google_admin,
    rate_limit,
    require_admin,
    unexpired,
)

router = APIRouter(prefix="/admin/telegram", dependencies=[Depends(require_admin)])
SessionDep = Annotated[Session, Depends(get_session)]


def administrator(request: Request, session: Session) -> Customer:
    customer = get_customer_by_session(request, session)
    if not is_google_admin(customer):
        raise HTTPException(403, "Увійдіть через Google-акаунт адміністратора.")
    return customer


@router.post("/link", response_model=None)
def begin(request: Request, session: SessionDep) -> JSONResponse | HTMLResponse:
    from app.main import ASSET_VERSION, templates

    administrator(request, session)
    if not settings.telegram_enabled:
        raise HTTPException(503, "Прив’язка Telegram тимчасово недоступна.")
    rate_limit(session, request, "telegram-link", 12, 300)
    lock_booking_writes(session)
    customer = administrator(request, session)
    bot_url = f"https://t.me/{settings.telegram_bot_username}"
    if session.get(TelegramAccount, customer.id) is None:
        session.execute(
            delete(TelegramLink).where(
                (TelegramLink.customer_id == customer.id) | (TelegramLink.expires_at < utc_now()),
            )
        )
        token = secrets.token_urlsafe(32)
        session.add(
            TelegramLink(
                token_hash=hash_secret(f"telegram-link:{token}"),
                customer_id=customer.id,
                session_hash=hash_session_token(
                    request.cookies.get(cookie_name(SESSION_COOKIE_NAME), "")
                ),
                expires_at=utc_now() + timedelta(minutes=10),
            )
        )
        bot_url += f"?start={token}"
    session.commit()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"url": bot_url})
    return templates.TemplateResponse(
        request,
        "telegram_link.html",
        {
            "asset_version": ASSET_VERSION,
            "bot_url": bot_url,
        },
    )


def pending_link(session: Session, token_hash: str) -> tuple[TelegramLink, Customer]:
    pending = session.get(TelegramLink, token_hash)
    if pending is None or not unexpired(pending.expires_at):
        raise HTTPException(
            410,
            "Посилання більше не діє. Натисніть «Прив’язати Telegram» у кабінеті адміністратора.",
        )
    customer = session.get(Customer, pending.customer_id)
    if not is_google_admin(customer):
        raise HTTPException(403, "Цей акаунт більше не має прав адміністратора.")
    login = session.exec(
        select(CustomerSession.id).where(
            CustomerSession.customer_id == customer.id,
            CustomerSession.token_hash == pending.session_hash,
            CustomerSession.expires_at > utc_now(),
        )
    ).first()
    if login is None:
        raise HTTPException(
            403, "Сеанс Google завершився. Увійдіть у кабінет і почніть прив’язку ще раз."
        )
    return pending, customer


def complete_link(
    session: Session, token_hash: str, telegram_id: int, *, username: str, name: str
) -> Customer:
    pending, customer = pending_link(session, token_hash)
    current = session.get(TelegramAccount, customer.id)
    account = session.exec(
        select(TelegramAccount).where(TelegramAccount.telegram_id == telegram_id)
    ).first()
    if current and current.telegram_id != telegram_id:
        raise HTTPException(
            409, "До кабінету вже прив’язаний інший Telegram. Спочатку від’єднайте його в кабінеті."
        )
    if account and account.customer_id != customer.id:
        raise HTTPException(409, "Цей Telegram уже прив’язаний до іншого акаунта.")
    if current is None:
        current = TelegramAccount(customer_id=customer.id, telegram_id=telegram_id)
    current.username, current.display_name = username[:64], name[:160]
    session.add(current)
    session.delete(pending)
    add_admin_audit(
        session,
        customer.email or f"Адміністратор №{customer.id}",
        "telegram_linked",
        "Прив’язано Telegram для керування записами",
    )
    return customer


@router.post("/unlink")
def unlink(request: Request, session: SessionDep) -> RedirectResponse:
    lock_booking_writes(session)
    customer = administrator(request, session)
    account = session.get(TelegramAccount, customer.id)
    if account:
        session.delete(account)
        session.execute(delete(TelegramLink).where(TelegramLink.customer_id == customer.id))
        session.execute(delete(TelegramView).where(TelegramView.customer_id == customer.id))
        add_admin_audit(
            session,
            customer.email or f"Адміністратор №{customer.id}",
            "telegram_unlinked",
            "Від’єднано Telegram від керування записами",
        )
    session.commit()
    return RedirectResponse("/cabinet", status_code=303)
