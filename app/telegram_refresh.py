"""Refresh only the active timetable, without holding the booking transaction lock."""

import asyncio
import logging
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, func, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.config import settings
from app.models import (
    Customer,
    TelegramAccount,
    TelegramAction,
    TelegramOutbox,
    TelegramState,
    TelegramView,
    utc_now,
)
from app.security import hash_secret, is_google_admin, unexpired
from app.telegram_chat import cleanup_chat, cleanup_chat_ids

REFRESH_SECONDS = 5
_locks: dict[int, threading.RLock] = {}
_locks_guard = threading.Lock()
logger = logging.getLogger(__name__)


@contextmanager
def chat_lock(engine: Engine, telegram_id: int, *, wait: bool = True) -> Iterator[bool]:
    """Serialize UI edits with navigation across workers; website booking writes stay free."""
    if engine.dialect.name == "postgresql":
        key = -int(hash_secret(f"telegram-ui:{telegram_id}")[:15], 16) - 1
        with engine.connect() as connection:
            function = func.pg_advisory_lock if wait else func.pg_try_advisory_lock
            result = connection.scalar(select(function(key)))
            acquired = wait or bool(result)
            connection.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(select(func.pg_advisory_unlock(key)))
                    connection.commit()
    else:
        with _locks_guard:
            lock = _locks.setdefault(telegram_id, threading.RLock())
        acquired = lock.acquire(blocking=wait)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()


def renew_buttons(session: Session, view: TelegramView) -> bool:
    tokens = [
        button["callback_data"]
        for row in view.payload.get("reply_markup", {}).get("inline_keyboard", [])
        for button in row
        if "callback_data" in button
    ]
    if not tokens:
        return True
    expiries = list(
        session.exec(
            select(TelegramAction.expires_at).where(
                TelegramAction.token.in_(tokens), TelegramAction.telegram_id == view.telegram_id
            )
        )
    )
    if len(expiries) != len(tokens):
        return False
    if any(not unexpired(expires - timedelta(minutes=5)) for expires in expiries):
        session.execute(
            update(TelegramAction)
            .where(TelegramAction.token.in_(tokens), TelegramAction.telegram_id == view.telegram_id)
            .values(expires_at=utc_now() + timedelta(minutes=20))
        )
    return True


def refresh_chat(session: Session, telegram_id: int) -> None:
    from app.telegram_bot import Bot, deliver_outbox

    if not deliver_outbox(session, telegram_id=telegram_id):
        return
    view = session.get(TelegramView, telegram_id)
    if view is None:
        return
    account = session.get(TelegramAccount, view.customer_id)
    customer = session.get(Customer, view.customer_id)
    if not is_google_admin(customer) or account is None or account.telegram_id != telegram_id:
        session.delete(view)
        session.commit()
        return
    state = session.get(TelegramState, telegram_id)
    if state and unexpired(state.expires_at):
        return
    if view.message_id is None:
        return
    bot = Bot(session, telegram_id, message_id=view.message_id, preview=True)
    bot.customer = customer
    context = view.context
    try:
        bot.day(
            date.fromisoformat(context["day"]),
            move=context.get("move"),
            version=context.get("version", ""),
            notice=context.get("notice", ""),
            time_page=context.get("time_page", 0),
        )
    except HTTPException:
        # Another admin can cancel/move the selected visit while this screen is open.
        try:
            bot.day(
                date.fromisoformat(context["day"]),
                notice="Запис або графік змінився. Перевірте розклад перед наступною дією.",
                time_page=context.get("time_page", 0),
            )
        except HTTPException:
            bot.show(
                "Огляд лікаря зараз недоступний. Розклад з’явиться після відкриття графіка.",
                view={"day": context["day"]},
            )
    if bot.fingerprint == view.fingerprint and renew_buttons(session, view):
        # Keep callback secrets alive without sending identical edits to Telegram.
        for item in list(session.new):
            session.expunge(item)
        session.commit()
        return
    bot.publish(is_refresh=True)
    session.commit()
    deliver_outbox(session, telegram_id=telegram_id)


def refresh_cycle(engine: Engine) -> None:
    with Session(engine) as session:
        ids = set(session.exec(select(TelegramView.telegram_id)))
        ids.update(cleanup_chat_ids(session))
        ids.update(
            session.exec(
                select(TelegramOutbox.telegram_id).where(TelegramOutbox.telegram_id.is_not(None))
            )
        )
        if (
            session.exec(
                select(TelegramOutbox.id).where(TelegramOutbox.telegram_id.is_(None))
            ).first()
            is not None
        ):
            ids.add(0)
    for telegram_id in ids:
        try:
            with chat_lock(engine, telegram_id, wait=False) as acquired:
                if acquired:
                    with Session(engine, autoflush=False) as session:
                        refresh_chat(session, telegram_id)
                        cleanup_chat(session, telegram_id)
        except Exception as exc:  # noqa: BLE001 -- isolate each chat and keep private data out of logs
            logger.warning("Telegram timetable refresh will retry after %s", type(exc).__name__)
    with Session(engine) as session:
        session.execute(delete(TelegramAction).where(TelegramAction.expires_at < utc_now()))
        session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import engine

    async def loop() -> None:
        while True:
            await asyncio.sleep(REFRESH_SECONDS)
            try:
                await asyncio.to_thread(refresh_cycle, engine)
            except Exception as exc:  # noqa: BLE001 -- keep the service alive after a database outage
                # HTTP exceptions can contain credential-bearing URLs; log only the type.
                logger.warning("Telegram refresh will retry after %s", type(exc).__name__)

    task = (
        asyncio.create_task(loop())
        if settings.telegram_enabled and settings.environment != "test"
        else None
    )
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
