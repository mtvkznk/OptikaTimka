import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory


def run_preview() -> None:
    with TemporaryDirectory(prefix="optika-browser-") as temp:
        origin = "http://127.0.0.1:8017"
        database = Path(temp) / "browser.sqlite"
        os.environ.update(
            DATABASE_URL="sqlite:///" + database.as_posix(),
            ENVIRONMENT="test",
            PUBLIC_BASE_URL=origin,
            GOOGLE_CLIENT_ID="browser-test-client",
            GOOGLE_CLIENT_SECRET="browser-test-secret",
            GOOGLE_REDIRECT_URI=origin + "/auth/google/callback",
            ADMIN_GOOGLE_EMAIL="owner@example.com",
            ADMIN_GOOGLE_EMAILS="",
            ADMIN_GOOGLE_SUBJECTS="",
            DOCTOR_DIARY_SERVICE_ID="",
            TELEGRAM_BOT_TOKEN="browser-test-token",
            TELEGRAM_BOT_USERNAME="OptykaTestBot",
            TELEGRAM_WEBHOOK_SECRET="browser-test-webhook-" + "a" * 32,
        )
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)

        import uvicorn
        from sqlmodel import Session

        from app import main
        from app.database import engine
        from app.models import Customer, CustomerSession, utc_now
        from app.security import hash_session_token

        main.kyiv_now = lambda: datetime(2026, 11, 18, 8, tzinfo=main.KYIV_TZ)
        try:
            with Session(engine) as session:
                main.ensure_seed_data(session)
                owner = Customer(email="owner@example.com", email_verified=True, google_sub="browser-owner")
                session.add(owner)
                session.flush()
                session.add(CustomerSession(
                    customer_id=owner.id,
                    token_hash=hash_session_token("g.browser-test"),
                    expires_at=utc_now() + timedelta(hours=1),
                ))
                session.commit()
            uvicorn.run(main.app, host="127.0.0.1", port=8017, log_level="warning")
        finally:
            engine.dispose()


if __name__ == "__main__":
    run_preview()
