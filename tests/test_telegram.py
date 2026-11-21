from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from sqlmodel import Session, select

from app import main, telegram_bot
from app.config import settings
from app.models import (
    Appointment,
    AppointmentStatus,
    BookingBlock,
    BookingService,
    Customer,
    CustomerSession,
    TelegramAccount,
    TelegramAction,
    TelegramAuth,
    TelegramOutbox,
    TelegramState,
    utc_now,
)
from app.security import SESSION_COOKIE_NAME, TELEGRAM_WEBHOOK_PATH, hash_session_token

TG_ID = 9000000011
SECRET = "test-webhook-secret-" + "a" * 32
NOW = datetime(2026, 11, 23, 8, 0, tzinfo=ZoneInfo("Europe/Kyiv"))


@pytest.fixture
def tg(client, engine, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("test-token"))
    monkeypatch.setattr(settings, "telegram_bot_username", "OptykaTestBot")
    monkeypatch.setattr(settings, "telegram_webhook_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "public_base_url", "https://optika.test")
    monkeypatch.setattr(main, "kyiv_now", lambda: NOW)
    client.get("/booking")
    sent = []
    monkeypatch.setattr(
        telegram_bot, "telegram_api", lambda method, payload: sent.append((method, payload)) or True
    )
    with Session(engine) as session:
        service = session.exec(select(BookingService).order_by(BookingService.id)).first()
        service_id = service.id
    monkeypatch.setattr(settings, "doctor_diary_service_id", service_id)
    return client, sent, service_id


def google_customer(client, engine, monkeypatch, *, link=True):
    monkeypatch.setattr(settings, "admin_google_email", "owner@example.com")
    token = "g.owner-session"
    with Session(engine) as session:
        customer = Customer(
            name="Owner", google_sub="owner-sub", email="owner@example.com", email_verified=True
        )
        session.add(customer)
        session.flush()
        session.add(
            CustomerSession(
                customer_id=customer.id,
                token_hash=hash_session_token(token),
                expires_at=utc_now() + timedelta(days=1),
            )
        )
        if link:
            session.add(TelegramAccount(customer_id=customer.id, telegram_id=TG_ID))
        session.commit()
        customer_id = customer.id
    client.cookies.set(SESSION_COOKIE_NAME, token, domain="testserver.local")
    return customer_id


def message(text, *, who=TG_ID):
    return {
        "message": {
            "message_id": 42,
            "from": {"id": who, "first_name": "Тест"},
            "chat": {"id": who, "type": "private"},
            "text": text,
        }
    }


def callback(token, *, who=TG_ID):
    return {
        "callback_query": {
            "id": "callback-id",
            "from": {"id": who},
            "data": token,
            "message": {"message_id": 42, "chat": {"id": who, "type": "private"}},
        }
    }


def send(client, payload, update_id=1):
    return client.post(
        TELEGRAM_WEBHOOK_PATH,
        json={"update_id": update_id, **payload},
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )


def button(sent, contains):
    for _, payload in reversed(sent):
        for row in payload.get("reply_markup", {}).get("inline_keyboard", []):
            for b in row:
                if contains in b["text"]:
                    return b["callback_data"]
    raise AssertionError(f"Button {contains} missing: {sent[-1:]}")


def test_webhook_rejects_spoofing_and_groups(tg, engine):
    client, sent, _ = tg
    assert client.post(TELEGRAM_WEBHOOK_PATH, json={"update_id": 1}).status_code == 403
    assert send(client, message("/start")).status_code == 200
    assert "прив’яжіть" in sent[-1][1]["text"]
    sent.clear()
    payload = message("/start")
    payload["message"]["chat"]["type"] = "group"
    assert send(client, payload, 2).status_code == 200
    assert sent == []


def test_link_login_and_unlink_revoke_telegram_sessions(tg, engine, monkeypatch):
    client, sent, _ = tg
    owner_id = google_customer(client, engine, monkeypatch, link=False)
    assert client.post("/auth/telegram/start").status_code == 200
    value = client.cookies.get("optika_telegram_auth")
    assert client.post("/auth/telegram/finish").status_code == 409
    assert send(client, message("/start " + value.partition(".")[0]), 11).status_code == 200
    assert client.get("/auth/telegram/status").json()["approved"] is False
    assert send(client, callback(button(sent, "Підтвердити")), 12).status_code == 200
    assert client.get("/auth/telegram/status").json()["approved"] is True
    assert client.post("/auth/telegram/finish").status_code == 200
    assert client.cookies.get(SESSION_COOKIE_NAME).startswith("t.")
    assert client.get("/admin").status_code == 200
    assert client.post("/auth/telegram/finish").status_code == 410
    with Session(engine) as session:
        assert session.get(TelegramAccount, owner_id).telegram_id == TG_ID
    assert client.post("/auth/telegram/unlink").status_code == 200
    assert client.get("/admin").status_code == 403
    sent.clear()
    assert send(client, message("/start"), 13).status_code == 200
    assert "прив’яжіть" in sent[-1][1]["text"]


def test_standalone_login_has_no_admin_or_verified_contact(tg, engine):
    client, sent, _ = tg
    client.post("/auth/telegram/start")
    token = client.cookies.get("optika_telegram_auth").partition(".")[0]
    send(client, message("/start " + token), 21)
    send(client, callback(button(sent, "Підтвердити")), 22)
    assert client.post("/auth/telegram/finish").status_code == 200
    with Session(engine) as session:
        account = session.exec(select(TelegramAccount)).one()
        customer = session.get(Customer, account.customer_id)
        assert (
            customer.google_sub is None and customer.email is None and not customer.email_verified
        )
        assert customer.phone is None and not customer.phone_verified
    # Basic auth is configured in this fixture; a Telegram customer doesn't replace it.
    client.auth = None
    assert client.get("/admin").status_code == 401


def test_expired_and_browser_mismatch_cannot_finish(tg, engine):
    client, _sent, _ = tg
    client.post("/auth/telegram/start")
    cookie = client.cookies.get("optika_telegram_auth")
    with Session(engine) as session:
        auth = session.exec(select(TelegramAuth)).one()
        auth.approved, auth.telegram_id = True, TG_ID
        session.commit()
    client.cookies.clear()
    assert client.get("/auth/telegram/status").status_code == 410
    client.cookies.set("optika_telegram_auth", cookie.partition(".")[0] + "." + "x" * 43)
    assert client.post("/auth/telegram/finish").status_code == 410
    client.cookies.clear()
    client.cookies.set("optika_telegram_auth", cookie)
    with Session(engine) as session:
        auth = session.exec(select(TelegramAuth)).one()
        auth.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    assert client.post("/auth/telegram/finish").status_code == 410


def test_bot_manual_future_new_duplicate_safe_and_customer_availability(tg, engine, monkeypatch):
    client, sent, service_id = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    send(client, callback(button(sent, "День →")), 2)
    send(client, callback(button(sent, "10:00 ＋")), 3)
    send(client, message("0981234567"), 4)
    payload = callback(button(sent, "Без імені"))
    assert send(client, payload, 5).status_code == 200
    assert send(client, payload, 5).status_code == 200
    with Session(engine) as session:
        visit = session.exec(select(Appointment)).one()
        assert visit.name == "" and visit.phone == "0981234567"
        assert visit.status == AppointmentStatus.new and visit.service_id == service_id
        day, service_name = visit.preferred_date, visit.service
    available = client.get(
        "/api/availability", params={"preferred_date": day.isoformat(), "service": service_name}
    ).json()
    assert all(row["start_time"] != "10:00:00" for row in available)


def test_same_day_name_only_and_admin_access_revocation(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    send(client, callback(button(sent, "10:00 ＋")), 2)
    send(client, callback(button(sent, "Без телефону")), 3)
    send(client, message("Пацієнт без телефону"), 4)
    with Session(engine) as session:
        visit = session.exec(select(Appointment)).one()
        assert visit.name == "Пацієнт без телефону" and visit.status == AppointmentStatus.confirmed
    monkeypatch.setattr(settings, "admin_google_email", "other@example.com")
    sent.clear()
    send(client, message("/records"), 5)
    assert "прив’яжіть" in sent[-1][1]["text"]


def test_close_reopen_and_occupied_rejection(tg, engine, monkeypatch):
    client, sent, service_id = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    oldclose = button(sent, "× Закрити")
    assert send(client, callback(oldclose), 2).status_code == 200
    with Session(engine) as session:
        block = session.exec(select(BookingBlock)).one()
        assert block.service_id == service_id and block.start_minute == 540
    send(client, callback(button(sent, "09:00 🔴")), 3)
    with Session(engine) as session:
        assert session.exec(select(BookingBlock)).first() is None
    send(client, callback(button(sent, "10:00 ＋")), 4)
    send(client, message("тест"), 5)
    send(client, callback(button(sent, "Без імені")), 6)
    # A stale close button from a different admin's view cannot close an occupied hour.
    with Session(engine) as session:
        session.add(
            TelegramAction(
                token="stale-close",
                telegram_id=TG_ID,
                expires_at=utc_now() + timedelta(minutes=10),
                payload={
                    "op": "block",
                    "day": NOW.date().isoformat(),
                    "time": "10:00",
                    "duration": 30,
                    "service_id": service_id,
                    "block_action": "close",
                },
            )
        )
        session.commit()
    send(client, callback("stale-close"), 7)
    assert "зайнятий" in sent[-1][1]["text"]
    with Session(engine) as session:
        assert session.exec(select(BookingBlock)).first() is None


def test_reschedule_preserves_patient_duration_and_status(tg, engine, monkeypatch):
    client, sent, service_id = tg
    google_customer(client, engine, monkeypatch)
    with Session(engine) as session:
        visit = Appointment(
            name="Пацієнт",
            phone="123",
            service="Доктор",
            service_id=service_id,
            preferred_date=NOW.date(),
            preferred_time=time(10),
            duration_minutes=30,
            status=AppointmentStatus.new,
        )
        session.add(visit)
        session.commit()
    send(client, message("/start"))
    send(client, callback(button(sent, "10:00 🟠")), 2)
    send(client, callback(button(sent, "Перенести")), 3)
    send(client, callback(button(sent, "День →")), 4)
    send(client, callback(button(sent, "11:00 ✓")), 5)
    with Session(engine) as session:
        visit = session.exec(select(Appointment)).one()
        assert visit.preferred_date == NOW.date() + timedelta(days=1)
        assert visit.preferred_time == time(11) and visit.phone == "123"
        assert visit.status == AppointmentStatus.new and visit.duration_minutes == 30


def test_future_deletion_confirmation_and_stale_status_protection(tg, engine, monkeypatch):
    client, sent, service_id = tg
    google_customer(client, engine, monkeypatch)
    with Session(engine) as session:
        service = session.get(BookingService, service_id)
        visit = Appointment(
            name="Client",
            phone="123",
            service=service.name,
            service_id=service_id,
            preferred_date=NOW.date() + timedelta(days=1),
            preferred_time=time(10),
        )
        session.add(visit)
        session.commit()
    send(client, message("/records"))
    send(client, callback(button(sent, "Client")), 2)
    oldstatus = button(sent, "Підтвердити")
    send(client, callback(button(sent, "Видалити")), 3)
    with Session(engine) as session:
        assert session.exec(select(Appointment)).first() is not None
    send(client, callback(button(sent, "Так, видалити")), 4)
    send(client, callback(oldstatus), 5)
    with Session(engine) as session:
        assert session.exec(select(Appointment)).first() is None


def test_rejected_callback_from_other_user_and_outbox_retry(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    action = button(sent, "10:00 ＋")
    send(client, callback(action, who=TG_ID + 1), 2)
    with Session(engine) as session:
        assert session.exec(select(TelegramState)).first() is None
    monkeypatch.setattr(telegram_bot, "telegram_api", lambda *args: False)
    update = message("/start")
    assert send(client, update, 3).status_code == 503
    with Session(engine) as session:
        assert session.exec(select(TelegramOutbox)).first() is not None
    monkeypatch.setattr(
        telegram_bot, "telegram_api", lambda method, payload: sent.append((method, payload)) or True
    )
    assert send(client, update, 3).status_code == 200
    with Session(engine) as session:
        assert session.exec(select(TelegramOutbox)).first() is None


def test_other_google_identity_cannot_take_an_existing_link(tg, engine, monkeypatch):
    client, sent, _ = tg
    owner = google_customer(client, engine, monkeypatch, link=False)
    with Session(engine) as session:
        other = Customer(google_sub="other", email="other@example.com", email_verified=True)
        session.add(other)
        session.flush()
        session.add(TelegramAccount(customer_id=other.id, telegram_id=TG_ID))
        session.commit()
        other_id = other.id
    client.post("/auth/telegram/start")
    token = client.cookies.get("optika_telegram_auth").partition(".")[0]
    send(client, message("/start " + token), 1)
    send(client, callback(button(sent, "Підтвердити")), 2)
    assert client.post("/auth/telegram/finish").status_code == 409
    with Session(engine) as session:
        assert session.get(TelegramAccount, owner) is None
        assert session.get(TelegramAccount, other_id).telegram_id == TG_ID


def test_new_telegram_account_can_merge_its_bookings_into_google(tg, engine, monkeypatch):
    client, sent, service_id = tg
    owner = google_customer(client, engine, monkeypatch, link=False)
    with Session(engine) as session:
        standalone = Customer(name="Telegram customer")
        session.add(standalone)
        session.flush()
        session.add(TelegramAccount(customer_id=standalone.id, telegram_id=TG_ID))
        session.add(
            Appointment(
                name="Test",
                phone="1",
                service="Exam",
                service_id=service_id,
                customer_id=standalone.id,
            )
        )
        session.commit()
    client.post("/auth/telegram/start")
    token = client.cookies.get("optika_telegram_auth").partition(".")[0]
    send(client, message("/start " + token), 1)
    send(client, callback(button(sent, "Підтвердити")), 2)
    assert client.post("/auth/telegram/finish").status_code == 200
    with Session(engine) as session:
        assert session.get(TelegramAccount, owner).telegram_id == TG_ID
        assert session.exec(select(Appointment)).one().customer_id == owner


def test_expired_callbacks_and_revoked_pending_replies(tg, engine, monkeypatch):
    client, sent, _ = tg
    owner = google_customer(client, engine, monkeypatch)
    send(client, message("/start"), 1)
    token = button(sent, "10:00 ＋")
    with Session(engine) as session:
        action = session.get(TelegramAction, token)
        action.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    send(client, callback(token), 2)
    assert "застаріла" in sent[-1][1]["text"]
    monkeypatch.setattr(telegram_bot, "telegram_api", lambda *args: False)
    assert send(client, message("/records"), 3).status_code == 503
    with Session(engine) as session:
        session.delete(session.get(TelegramAccount, owner))
        session.commit()
    sent.clear()
    monkeypatch.setattr(
        telegram_bot, "telegram_api", lambda method, payload: sent.append((method, payload)) or True
    )
    assert send(client, message("/records"), 3).status_code == 200
    assert sent == []


def test_blank_manual_entry_cannot_book_and_changed_duration_is_rejected(tg, engine, monkeypatch):
    client, sent, service_id = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"), 1)
    send(client, callback(button(sent, "10:00 ＋")), 2)
    send(client, callback(button(sent, "Без телефону")), 3)
    send(client, callback(button(sent, "Без імені")), 4)
    with Session(engine) as session:
        assert session.exec(select(Appointment)).first() is None
        service = session.get(BookingService, service_id)
        service.duration_minutes = 60
        session.commit()
    send(client, message("Name"), 5)
    assert "Тривалість" in sent[-1][1]["text"]
    with Session(engine) as session:
        assert session.exec(select(Appointment)).first() is None
