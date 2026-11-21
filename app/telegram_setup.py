"""Run explicitly after deployment: python -m app.telegram_setup."""

import sys

import httpx

from app.config import settings
from app.security import TELEGRAM_WEBHOOK_PATH


def configure() -> None:
    if not settings.telegram_enabled or not settings.public_base_url:
        raise RuntimeError("Configure the Telegram credentials and PUBLIC_BASE_URL first")
    with httpx.Client(timeout=20) as client:

        def call(method: str, **payload):
            response = client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/{method}",
                json=payload,
            ).json()
            if not response.get("ok"):
                raise RuntimeError(
                    f"Telegram rejected {method} (code {response.get('error_code')})"
                )
            return response["result"]

        profile = call("getMe")
        if profile.get("username", "").casefold() != settings.telegram_bot_username.casefold():
            raise RuntimeError("The configured bot username does not match its token")
        target = settings.public_base_url + TELEGRAM_WEBHOOK_PATH
        webhook = call("getWebhookInfo")
        if webhook.get("url") not in {"", target}:
            raise RuntimeError(
                "The bot already has a different webhook; review it before replacing"
            )
        call(
            "setMyCommands",
            commands=[
                {"command": "start", "description": "Розклад огляду лікаря"},
                {"command": "records", "description": "Усі записи та пошук"},
                {"command": "cancel", "description": "Скасувати поточну дію"},
                {"command": "help", "description": "Як користуватися ботом"},
            ],
        )
        call(
            "setWebhook",
            url=target,
            secret_token=settings.telegram_webhook_secret.get_secret_value(),
            allowed_updates=["message", "callback_query"],
            max_connections=1,
            drop_pending_updates=False,
        )
        if call("getWebhookInfo").get("url") != target:
            raise RuntimeError("The webhook URL was not saved")


if __name__ == "__main__":
    try:
        configure()
    except (RuntimeError, httpx.HTTPError, ValueError) as exc:
        # HTTP exception text includes the bot token in its URL.
        print(
            str(exc)
            if isinstance(exc, RuntimeError)
            else "Could not contact Telegram; credentials were not logged.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("Telegram commands and webhook configured. Pending updates were preserved.")
