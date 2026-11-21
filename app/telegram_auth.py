"""Browser-bound, single-use Telegram approval; no email claims from Telegram."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, update
from sqlmodel import Session, select

from app.admin_audit import add_admin_audit
from app.booking import lock_booking_writes
from app.config import settings
from app.database import get_session
from app.models import (
    Appointment,
    Customer,
    CustomerSession,
    TelegramAccount,
    TelegramAuth,
    utc_now,
)
from app.security import (
    SESSION_COOKIE_NAME,
    cookie_name,
    get_customer_by_session,
    hash_secret,
    hash_session_token,
    production,
    rate_limit,
)

router = APIRouter(prefix="/auth/telegram")
SessionDep = Annotated[Session, Depends(get_session)]
AUTH_COOKIE = "optika_telegram_auth"


def unexpired(value: datetime) -> bool:
    return value.replace(tzinfo=value.tzinfo or UTC) > utc_now()


def enabled() -> None:
    if not settings.telegram_enabled:
        raise HTTPException(503, "Вхід через Telegram тимчасово недоступний.")


def browser_auth(request: Request, session: Session) -> tuple[TelegramAuth, str]:
    enabled()
    value = request.cookies.get(cookie_name(AUTH_COOKIE), "")
    pieces = value.split(".")
    if len(pieces) != 2 or any(len(piece) != 43 for piece in pieces):
        raise HTTPException(410, "Посилання для входу закінчилося. Почніть ще раз у кабінеті.")
    token, browser = pieces
    pending = session.get(TelegramAuth, hash_secret(f"telegram-auth:{token}"))
    if (
        pending is None
        or not unexpired(pending.expires_at)
        or not secrets.compare_digest(
            pending.browser_hash,
            hash_secret(f"telegram-browser:{browser}"),
        )
    ):
        raise HTTPException(410, "Посилання для входу закінчилося. Почніть ще раз у кабінеті.")
    current = request.cookies.get(cookie_name(SESSION_COOKIE_NAME), "")
    if pending.session_hash != hash_session_token(current):
        raise HTTPException(409, "Акаунт у браузері змінився. Почніть прив’язку ще раз.")
    if pending.requester_id is not None:
        customer = get_customer_by_session(request, session)
        if customer is None or customer.id != pending.requester_id:
            raise HTTPException(403, "Увійдіть у свій кабінет ще раз.")
    return pending, token


@router.post("/start")
def begin(request: Request, session: SessionDep) -> RedirectResponse:
    enabled()
    rate_limit(session, request, "telegram-auth", 12, 300)
    lock_booking_writes(session)
    customer = get_customer_by_session(request, session)
    old_value = request.cookies.get(cookie_name(AUTH_COOKIE), "").partition(".")[0]
    if old_value:
        session.execute(
            delete(TelegramAuth).where(
                TelegramAuth.token_hash == hash_secret(f"telegram-auth:{old_value}"),
            )
        )
    session.execute(delete(TelegramAuth).where(TelegramAuth.expires_at < utc_now()))
    token, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session.add(
        TelegramAuth(
            token_hash=hash_secret(f"telegram-auth:{token}"),
            browser_hash=hash_secret(f"telegram-browser:{browser}"),
            requester_id=customer.id if customer else None,
            session_hash=hash_session_token(
                request.cookies.get(cookie_name(SESSION_COOKIE_NAME), "")
            ),
            expires_at=utc_now() + timedelta(minutes=10),
        )
    )
    session.commit()
    response = RedirectResponse("/auth/telegram", status_code=303)
    response.set_cookie(
        cookie_name(AUTH_COOKIE),
        f"{token}.{browser}",
        max_age=600,
        httponly=True,
        secure=production(),
        samesite="lax",
        path="/",
    )
    return response


@router.get("", response_class=HTMLResponse)
def pending_page(request: Request, session: SessionDep) -> HTMLResponse:
    from app.main import ASSET_VERSION, templates

    pending, token = browser_auth(request, session)
    return templates.TemplateResponse(
        request,
        "telegram_auth.html",
        {
            "app_name": settings.app_name,
            "asset_version": ASSET_VERSION,
            "linking": pending.requester_id is not None,
            "bot_url": f"https://t.me/{settings.telegram_bot_username}?start={token}",
        },
    )


@router.get("/status")
def auth_status(request: Request, session: SessionDep) -> dict:
    pending, _ = browser_auth(request, session)
    return {"approved": pending.approved, "name": pending.display_name if pending.approved else ""}


def complete_identity(session: Session, pending: TelegramAuth) -> Customer:
    account = session.exec(
        select(TelegramAccount).where(
            TelegramAccount.telegram_id == pending.telegram_id,
        )
    ).first()
    if pending.requester_id is not None:
        customer = session.get(Customer, pending.requester_id)
        current = session.get(TelegramAccount, customer.id)
        if current and current.telegram_id != pending.telegram_id:
            raise HTTPException(
                409, "До цього кабінету вже прив’язаний інший Telegram. Спочатку від’єднайте його."
            )
        if account and account.customer_id != customer.id:
            previous = session.get(Customer, account.customer_id)
            if previous.google_sub:
                raise HTTPException(409, "Цей Telegram уже прив’язаний до іншого Google-акаунта.")
            # Both identities were proved. Preserve bookings from a Telegram-only account.
            session.execute(
                update(Appointment)
                .where(Appointment.customer_id == previous.id)
                .values(customer_id=customer.id)
            )
            session.execute(
                delete(CustomerSession).where(CustomerSession.customer_id == previous.id)
            )
            session.delete(account)
            session.flush()
            account = None
    elif account:
        customer = session.get(Customer, account.customer_id)
    else:
        customer = Customer(name=pending.display_name)
        session.add(customer)
        session.flush()
    if account is None:
        account = TelegramAccount(customer_id=customer.id, telegram_id=pending.telegram_id)
    account.username = pending.username
    account.display_name = pending.display_name
    customer.last_login_at = utc_now()
    customer.updated_at = utc_now()
    session.add(account)
    if pending.requester_id is not None:
        add_admin_audit(
            session,
            customer.email or f"Клієнт №{customer.id}",
            "telegram_linked",
            "Прив’язано особистий Telegram-акаунт",
        )
    return customer


@router.post("/finish")
def finish(request: Request, session: SessionDep) -> RedirectResponse:
    from app.main import issue_customer_session

    lock_booking_writes(session)
    pending, _ = browser_auth(request, session)
    if not pending.approved or pending.telegram_id is None:
        raise HTTPException(409, "Спочатку підтвердьте дію в Telegram.")
    customer = complete_identity(session, pending)
    session.delete(pending)
    session.flush()
    response = RedirectResponse("/cabinet", status_code=303)
    issue_customer_session(customer, session, response, request, provider="telegram")
    response.delete_cookie(
        cookie_name(AUTH_COOKIE), path="/", secure=production(), httponly=True, samesite="lax"
    )
    return response


@router.post("/unlink")
def unlink(request: Request, session: SessionDep) -> RedirectResponse:
    lock_booking_writes(session)
    customer = get_customer_by_session(request, session)
    if customer is None or not customer.google_sub or not customer.email_verified:
        raise HTTPException(
            403, "Для від’єднання потрібен кабінет із підтвердженим Google-акаунтом."
        )
    account = session.get(TelegramAccount, customer.id)
    if account:
        session.delete(account)
        session.execute(
            delete(CustomerSession).where(
                CustomerSession.customer_id == customer.id, CustomerSession.provider == "telegram"
            )
        )
        session.execute(delete(TelegramAuth).where(TelegramAuth.requester_id == customer.id))
        add_admin_audit(
            session,
            customer.email or f"Клієнт №{customer.id}",
            "telegram_unlinked",
            "Від’єднано Telegram і завершено його сеанси",
        )
    session.commit()
    return RedirectResponse("/cabinet", status_code=303)
