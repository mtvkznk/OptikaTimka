"""Remove only observed workflow messages after their replacement is delivered."""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, update
from sqlmodel import Session, select

from app.models import TelegramChat, TelegramMessage, TelegramOutbox, TelegramState, utc_now
from app.telegram_auth import unexpired

INPUT_MODES = frozenset({"phone", "name", "date", "search"})
COMMANDS = frozenset({"/start", "/today", "/records", "/cancel", "/help"})
DELETE_AGE = timedelta(hours=47, minutes=55)


def chat_record(session: Session, telegram_id: int) -> TelegramChat:
    chat = session.get(TelegramChat, telegram_id)
    if chat is None:
        chat = TelegramChat(telegram_id=telegram_id)
        session.add(chat)
    return chat


def plan_screen(session: Session, telegram_id: int) -> str:
    chat = chat_record(session, telegram_id)
    chat.generation = secrets.token_urlsafe(18)
    return chat.generation


def remember(
    session: Session,
    telegram_id: int,
    message_id: int,
    *,
    is_ui: bool,
    pending: bool = False,
    created_at: datetime | None = None,
) -> None:
    if type(message_id) is not int or message_id <= 0:
        return
    row = session.get(TelegramMessage, (telegram_id, message_id))
    created_at = created_at or utc_now()
    if row is None:
        session.add(
            TelegramMessage(
                telegram_id=telegram_id,
                message_id=message_id,
                is_ui=is_ui,
                pending=pending,
                created_at=created_at,
            )
        )
    else:
        row.is_ui = row.is_ui or is_ui
        row.pending = row.pending or pending
        if created_at < row.created_at.replace(tzinfo=row.created_at.tzinfo or UTC):
            row.created_at = created_at


def record_update(session: Session, update: dict) -> None:
    callback = update.get("callback_query") or {}
    message = callback.get("message") or update.get("message") or {}
    user = (callback or message).get("from", {})
    telegram_id = user.get("id")
    if (
        type(telegram_id) is not int
        or telegram_id <= 0
        or user.get("is_bot")
        or message.get("chat", {}).get("type") != "private"
        or message["chat"]["id"] != telegram_id
    ):
        return
    state = session.get(TelegramState, telegram_id)
    text = str(message.get("text") or "").strip()
    if not callback and (
        not text
        or (
            text.partition(" ")[0] not in COMMANDS
            and not (state and state.payload.get("mode") in INPUT_MODES)
        )
    ):
        return
    created_at = None
    if type(message.get("date")) is int:
        try:
            created_at = datetime.fromtimestamp(message["date"], UTC)
        except (ValueError, OverflowError, OSError):
            pass
    pending = bool(state and state.payload.get("mode") in INPUT_MODES)
    remember(
        session,
        telegram_id,
        message.get("message_id"),
        is_ui=bool(callback),
        pending=pending,
        created_at=created_at,
    )


def settle_inputs(session: Session, telegram_id: int) -> None:
    if session.get(TelegramState, telegram_id) is None:
        session.execute(
            update(TelegramMessage)
            .where(TelegramMessage.telegram_id == telegram_id)
            .values(pending=False)
        )


def delivered_screen(
    session: Session, telegram_id: int, message_id: int, *, created_at: datetime | None = None
) -> None:
    chat = chat_record(session, telegram_id)
    chat.message_id = message_id
    chat.delivered_generation = chat.generation
    state = session.get(TelegramState, telegram_id)
    remember(
        session,
        telegram_id,
        message_id,
        is_ui=True,
        pending=bool(state and state.payload.get("mode") in INPUT_MODES),
        created_at=created_at,
    )


def cleanup_chat(session: Session, telegram_id: int) -> None:
    from app.telegram_bot import telegram_api

    chat = session.get(TelegramChat, telegram_id)
    if chat is None or (chat.cleanup_after and unexpired(chat.cleanup_after)):
        return
    if chat.message_id is None:
        session.execute(
            delete(TelegramMessage).where(
                TelegramMessage.telegram_id == telegram_id,
                TelegramMessage.created_at < utc_now() - DELETE_AGE,
            )
        )
        session.commit()
        return
    # A failed send must never cause the previous screen or unsaved input to disappear.
    if (
        session.exec(
            select(TelegramOutbox.id).where(
                TelegramOutbox.telegram_id == telegram_id,
                TelegramOutbox.method.in_(["sendMessage", "editMessageText"]),
            )
        ).first()
        is not None
    ):
        return
    old = utc_now() - DELETE_AGE
    session.execute(
        delete(TelegramMessage).where(
            TelegramMessage.telegram_id == telegram_id,
            TelegramMessage.message_id != chat.message_id,
            TelegramMessage.created_at < old,
        )
    )
    if chat.generation != chat.delivered_generation:
        session.commit()
        return
    rows = list(
        session.exec(
            select(TelegramMessage)
            .where(
                TelegramMessage.telegram_id == telegram_id,
                TelegramMessage.message_id != chat.message_id,
                ~TelegramMessage.pending,
            )
            .order_by(TelegramMessage.created_at, TelegramMessage.message_id)
            .limit(100)
        )
    )
    if not rows:
        session.commit()
        return
    result = telegram_api(
        "deleteMessages", {"chat_id": telegram_id, "message_ids": [row.message_id for row in rows]}
    )
    if not result and result.error_code == 400:
        # An individually undeletable message must not strand all other known IDs.
        for row in rows[:10]:
            result = telegram_api(
                "deleteMessage", {"chat_id": telegram_id, "message_id": row.message_id}
            )
            if result or result.terminal:
                session.delete(row)
            if result.error_code == 403:
                chat.cleanup_after = utc_now() + timedelta(minutes=1)
                break
            if not result and not result.terminal:
                chat.cleanup_after = utc_now() + timedelta(seconds=result.retry_after)
                break
    elif result or result.terminal:
        for row in rows:
            session.delete(row)
    else:
        chat.cleanup_after = utc_now() + timedelta(seconds=result.retry_after)
    session.commit()


def cleanup_chat_ids(session: Session) -> set[int]:
    old = utc_now() - DELETE_AGE
    return set(
        session.exec(
            select(TelegramMessage.telegram_id)
            .join(TelegramChat, TelegramChat.telegram_id == TelegramMessage.telegram_id)
            .where(
                or_(
                    TelegramChat.message_id.is_(None),
                    TelegramMessage.message_id != TelegramChat.message_id,
                ),
                or_(
                    (~TelegramMessage.pending)
                    & TelegramChat.message_id.is_not(None)
                    & (TelegramChat.generation == TelegramChat.delivered_generation),
                    TelegramMessage.created_at < old,
                ),
                or_(TelegramChat.cleanup_after.is_(None), TelegramChat.cleanup_after <= utc_now()),
            )
            .distinct()
        )
    )
