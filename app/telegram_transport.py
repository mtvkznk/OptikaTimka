"""Bot API results without credential-bearing URLs in exceptions or logs."""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.config import settings


@dataclass(frozen=True)
class TelegramReply:
    ok: bool
    message_id: int | None = None
    terminal: bool = False
    missing_message: bool = False
    retry_after: int = 10
    error_code: int | None = None
    message_date: datetime | None = None

    def __bool__(self) -> bool:
        return self.ok


def telegram_api(method: str, payload: dict) -> TelegramReply:
    if not settings.telegram_bot_token:
        return TelegramReply(False)
    try:
        with httpx.Client(timeout=12) as client:
            result = client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/{method}",
                json=payload,
            ).json()
        if result.get("ok"):
            message = result.get("result")
            message_id = (
                message.get("message_id")
                if isinstance(message, dict)
                else payload.get("message_id")
            )
            timestamp = message.get("date") if isinstance(message, dict) else None
            return TelegramReply(
                True,
                message_id=message_id,
                message_date=datetime.fromtimestamp(timestamp, UTC)
                if type(timestamp) is int
                else None,
            )
        code = result.get("error_code")
        description = str(result.get("description", "")).lower()
        if code == 400 and method == "editMessageText" and "message is not modified" in description:
            return TelegramReply(True, message_id=payload.get("message_id"))
        if code == 400 and method == "answerCallbackQuery":
            return TelegramReply(True)
        missing = (
            code == 400
            and method == "editMessageText"
            and any(
                text in description
                for text in (
                    "message to edit not found",
                    "message can't be edited",
                    "message identifier is not specified",
                )
            )
        )
        return TelegramReply(
            False,
            terminal=code in {400, 403},
            missing_message=missing,
            retry_after=max(1, min(3600, int(result.get("parameters", {}).get("retry_after", 10)))),
            error_code=code,
        )
    except (httpx.HTTPError, ValueError, OverflowError, OSError):
        return TelegramReply(False)
