import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from urllib.parse import parse_qs, urlparse
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
    ServiceSchedule,
    TelegramAccount,
    TelegramAction,
    TelegramChat,
    TelegramLink,
    TelegramMessage,
    TelegramOutbox,
    TelegramState,
    TelegramView,
    utc_now,
)
from app.security import SESSION_COOKIE_NAME, TELEGRAM_WEBHOOK_PATH, hash_session_token
from app.telegram_chat import cleanup_chat, remember
from app.telegram_refresh import chat_lock, refresh_cycle
from app.telegram_transport import TelegramReply

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

    def api_reply(method, payload):
        if method not in {"deleteMessages", "deleteMessage"}:
            sent.append((method, payload))
        return TelegramReply(True, message_id=payload.get("message_id", 42))

    monkeypatch.setattr(
        telegram_bot,
        "telegram_api",
        api_reply,
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


def callback(token, *, who=TG_ID, message_id=42):
    return {
        "callback_query": {
            "id": "callback-id",
            "from": {"id": who},
            "data": token,
            "message": {"message_id": message_id, "chat": {"id": who, "type": "private"}},
        }
    }


def send(client, payload, update_id=1):
    if "message" in payload and payload["message"].get("message_id") == 42:
        payload = {**payload, "message": {**payload["message"], "message_id": 10000 + update_id}}
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


def begin_link(client):
    response = client.post("/admin/telegram/link", headers={"Accept": "application/json"})
    assert response.status_code == 200
    url = urlparse(response.json()["url"])
    assert url.netloc == "t.me" and url.path == "/OptykaTestBot"
    return parse_qs(url.query)["start"][0]


def test_admin_link_completes_in_bot_without_website_login(tg, engine, monkeypatch):
    client, sent, _ = tg
    owner_id = google_customer(client, engine, monkeypatch, link=False)
    token = begin_link(client)
    assert send(client, message("/start " + token), 11).status_code == 200
    assert send(client, callback(button(sent, "Підтвердити")), 12).status_code == 200
    assert client.cookies.get(SESSION_COOKIE_NAME) == "g.owner-session"
    assert client.get("/admin").status_code == 200
    assert "Можна керувати записами" in sent[-1][1]["text"]
    with Session(engine) as session:
        assert session.get(TelegramAccount, owner_id).telegram_id == TG_ID
        assert len(session.exec(select(CustomerSession)).all()) == 1
        assert session.exec(select(TelegramLink)).first() is None
    assert client.post("/admin/telegram/link", headers={"Accept": "application/json"}).json() == {
        "url": "https://t.me/OptykaTestBot"
    }
    assert client.post("/admin/telegram/unlink").status_code == 200
    assert client.get("/admin").status_code == 200
    sent.clear()
    assert send(client, message("/start"), 13).status_code == 200
    assert "прив’яжіть" in sent[-1][1]["text"]


def test_public_and_regular_google_customers_cannot_link(tg, engine, monkeypatch):
    client, _sent, _ = tg
    assert "Telegram" not in client.get("/cabinet").text
    assert client.post("/admin/telegram/link").status_code == 403
    assert client.post("/admin/telegram/unlink").status_code == 403
    for path in ["/auth/telegram", "/auth/telegram/status"]:
        assert client.get(path).status_code == 404
    for path in ["/auth/telegram/start", "/auth/telegram/finish", "/auth/telegram/unlink"]:
        assert client.post(path).status_code == 404
    google_customer(client, engine, monkeypatch, link=False)
    monkeypatch.setattr(settings, "admin_google_email", "another-owner@example.com")
    assert "Telegram" not in client.get("/cabinet").text
    assert client.post("/admin/telegram/link").status_code == 403
    assert client.post("/admin/telegram/unlink").status_code == 403
    with Session(engine) as session:
        assert session.exec(select(TelegramAccount)).first() is None
        assert session.exec(select(TelegramLink)).first() is None


def test_expired_link_and_logged_out_google_session_cannot_link(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch, link=False)
    token = begin_link(client)
    with Session(engine) as session:
        pending = session.exec(select(TelegramLink)).one()
        pending.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    send(client, message("/start " + token), 1)
    assert "Посилання більше не діє" in sent[-1][1]["text"]
    token = begin_link(client)
    send(client, message("/start " + token), 2)
    accept = button(sent, "Підтвердити")
    assert client.post("/auth/logout").status_code == 200
    send(client, callback(accept), 3)
    assert "Сеанс Google завершився" in sent[-1][1]["text"]
    with Session(engine) as session:
        assert session.exec(select(TelegramAccount)).first() is None
        assert session.exec(select(CustomerSession)).first() is None


def test_bot_manual_future_new_duplicate_safe_and_customer_availability(tg, engine, monkeypatch):
    client, sent, service_id = tg
    google_customer(client, engine, monkeypatch)
    with Session(engine) as session:
        session.add(ServiceSchedule(service_id=service_id, intervals=[(14 * 60, 21 * 60)]))
        session.commit()
    send(client, message("/start"))
    send(client, callback(button(sent, "День →")), 2)
    # The last hour of a fourteen-slot day is available without paging.
    send(client, callback(button(sent, "20:30 ＋")), 3)
    send(client, message("0981234567"), 4)
    payload = callback(button(sent, "Без імені"))
    assert send(client, payload, 5).status_code == 200
    assert send(client, payload, 5).status_code == 200
    with Session(engine) as session:
        visit = session.exec(select(Appointment)).one()
        assert visit.name == "" and visit.phone == "0981234567"
        assert visit.status == AppointmentStatus.new and visit.service_id == service_id
        assert visit.preferred_time == time(20, 30)
        day, service_name = visit.preferred_date, visit.service
    available = client.get(
        "/api/availability", params={"preferred_date": day.isoformat(), "service": service_name}
    ).json()
    assert all(row["start_time"] != "20:30:00" for row in available)


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
    monkeypatch.setattr(telegram_bot, "telegram_api", lambda *args: TelegramReply(False))
    update = message("/start")
    assert send(client, update, 3).status_code == 503
    with Session(engine) as session:
        assert session.exec(select(TelegramOutbox)).first() is not None
        for row in session.exec(select(TelegramOutbox)):
            row.not_before = utc_now() - timedelta(seconds=1)
        session.commit()
    monkeypatch.setattr(
        telegram_bot,
        "telegram_api",
        lambda method, payload: (
            sent.append((method, payload)) or TelegramReply(True, message_id=42)
        ),
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
    token = begin_link(client)
    send(client, message("/start " + token), 1)
    send(client, callback(button(sent, "Підтвердити")), 2)
    assert "уже прив’язаний до іншого" in sent[-1][1]["text"]
    with Session(engine) as session:
        assert session.get(TelegramAccount, owner) is None
        assert session.get(TelegramAccount, other_id).telegram_id == TG_ID


def test_link_rechecks_role_and_is_single_use(tg, engine, monkeypatch):
    client, sent, _ = tg
    owner = google_customer(client, engine, monkeypatch, link=False)
    old_token = begin_link(client)
    send(client, message("/start " + old_token), 1)
    accept = button(sent, "Підтвердити")
    monkeypatch.setattr(settings, "admin_google_email", "another-owner@example.com")
    send(client, callback(accept), 2)
    assert "більше не має прав" in sent[-1][1]["text"]
    monkeypatch.setattr(settings, "admin_google_email", "owner@example.com")
    token = begin_link(client)
    send(client, message("/start " + old_token), 3)
    assert "Посилання більше не діє" in sent[-1][1]["text"]
    send(client, message("/start " + token), 4)
    accept = button(sent, "Підтвердити")
    send(client, callback(accept, who=TG_ID + 1), 5)
    with Session(engine) as session:
        assert session.get(TelegramAccount, owner) is None
    send(client, callback(accept), 6)
    send(client, message("/start " + token, who=TG_ID + 1), 7)
    assert "Посилання більше не діє" in sent[-1][1]["text"]
    with Session(engine) as session:
        assert session.get(TelegramAccount, owner).telegram_id == TG_ID
        assert len(session.exec(select(Customer)).all()) == 1
        assert len(session.exec(select(CustomerSession)).all()) == 1


def test_old_telegram_cookie_cannot_authenticate_even_with_valid_session(tg, engine, monkeypatch):
    client, _sent, _ = tg
    owner = google_customer(client, engine, monkeypatch)
    token = "t.former-web-session"
    with Session(engine) as session:
        session.add(
            CustomerSession(
                customer_id=owner, token_hash=hash_session_token(token),
                expires_at=utc_now() + timedelta(days=1),
            )
        )
        session.commit()
    client.cookies.set(SESSION_COOKIE_NAME, token, domain="testserver.local")
    assert client.get("/admin").status_code == 403
    assert client.post("/admin/telegram/link").status_code == 403
    assert "Вхід у кабінет" in client.get("/cabinet").text


def test_no_javascript_fallback_and_unavailable_diary_still_link(tg, engine, monkeypatch):
    client, sent, _ = tg
    owner = google_customer(client, engine, monkeypatch, link=False)
    response = client.post("/admin/telegram/link")
    assert response.status_code == 200
    assert "Відкрити Telegram" in response.text and "telegram-finish" not in response.text
    token = begin_link(client)
    send(client, message("/start " + token), 1)
    monkeypatch.setattr(settings, "doctor_diary_service_id", 999999)
    send(client, callback(button(sent, "Підтвердити")), 2)
    assert "Telegram прив’язано" in sent[-1][1]["text"]
    with Session(engine) as session:
        assert session.get(TelegramAccount, owner).telegram_id == TG_ID


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
    monkeypatch.setattr(telegram_bot, "telegram_api", lambda *args: TelegramReply(False))
    assert send(client, message("/records"), 3).status_code == 503
    with Session(engine) as session:
        session.delete(session.get(TelegramAccount, owner))
        session.commit()
    sent.clear()
    monkeypatch.setattr(
        telegram_bot,
        "telegram_api",
        lambda method, payload: (
            sent.append((method, payload)) or TelegramReply(True, message_id=42)
        ),
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


def web_manual(client, starts="10:00", *, day=None, contact="Refresh patient"):
    response = client.post(
        "/admin/appointments/manual",
        data={
            "preferred_date": (day or NOW.date()).isoformat(),
            "preferred_time": starts,
            "expected_duration_minutes": "30",
            "phone": contact,
        },
    )
    assert response.status_code == 200


def test_website_booking_refreshes_same_message_only_on_change(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    with Session(engine) as session:
        view = session.get(TelegramView, TG_ID)
        assert view.message_id == 42
        assert all(
            "Оновити" not in b["text"]
            for row in view.payload["reply_markup"]["inline_keyboard"]
            for b in row
        )
    web_manual(client)
    sent.clear()
    refresh_cycle(engine)
    assert len(sent) == 1 and sent[0][0] == "editMessageText"
    assert sent[0][1]["message_id"] == 42
    assert "Refresh patient" in str(sent[0][1])
    with Session(engine) as session:
        count = len(session.exec(select(TelegramAction)).all())
    sent.clear()
    refresh_cycle(engine)
    assert sent == []
    with Session(engine) as session:
        assert len(session.exec(select(TelegramAction)).all()) == count


def test_refresh_pauses_during_manual_input_and_resumes_after_save(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    send(client, callback(button(sent, "11:00 ＋")), 2)
    sent.clear()
    web_manual(client)
    refresh_cycle(engine)
    assert sent == []
    with Session(engine) as session:
        assert session.get(TelegramView, TG_ID) is None
        assert session.get(TelegramState, TG_ID).payload["mode"] == "phone"
    send(client, message("123"), 3)
    send(client, callback(button(sent, "Без імені")), 4)
    with Session(engine) as session:
        assert session.get(TelegramView, TG_ID) is not None


def test_refresh_preserves_move_mode_and_handles_external_cancellation(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch)
    web_manual(client)
    send(client, message("/start"))
    send(client, callback(button(sent, "10:00 🟢")), 2)
    send(client, callback(button(sent, "Перенести")), 3)
    send(client, callback(button(sent, "День →")), 4)
    web_manual(client, "11:00", day=NOW.date() + timedelta(days=1))
    sent.clear()
    refresh_cycle(engine)
    assert len(sent) == 1
    assert "Перенесення" in sent[0][1]["text"]
    assert "11:00 ✓" not in str(sent[0][1])
    with Session(engine) as session:
        view = session.get(TelegramView, TG_ID)
        assert view.context["move"] is not None
        visit = session.get(Appointment, view.context["move"])
        visit.status = AppointmentStatus.canceled
        visit.updated_at = utc_now()
        session.commit()
    sent.clear()
    refresh_cycle(engine)
    with Session(engine) as session:
        assert session.get(TelegramView, TG_ID).context["move"] is None
    assert "Перенесення прийому" not in sent[0][1]["text"]


def test_refresh_retries_after_rate_limit_without_replacing_message(tg, engine, monkeypatch):
    client, _sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    web_manual(client)
    attempts = []
    monkeypatch.setattr(
        telegram_bot,
        "telegram_api",
        lambda method, payload: attempts.append(method) or TelegramReply(False, retry_after=30),
    )
    refresh_cycle(engine)
    refresh_cycle(engine)
    assert attempts == ["editMessageText"]
    with Session(engine) as session:
        row = session.exec(select(TelegramOutbox)).one()
        assert row.is_refresh
        row.not_before = utc_now() - timedelta(seconds=1)
        session.commit()
    monkeypatch.setattr(
        telegram_bot,
        "telegram_api",
        lambda method, payload: attempts.append(method) or TelegramReply(True, message_id=42),
    )
    refresh_cycle(engine)
    assert [method for method in attempts if not method.startswith("delete")] == [
        "editMessageText",
        "editMessageText",
    ]
    with Session(engine) as session:
        assert session.exec(select(TelegramOutbox)).first() is None


def test_deleted_telegram_message_stops_refresh_without_sending_a_new_one(tg, engine, monkeypatch):
    client, _sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    web_manual(client)
    attempts = []
    monkeypatch.setattr(
        telegram_bot,
        "telegram_api",
        lambda method, payload: (
            attempts.append(method) or TelegramReply(False, terminal=True, missing_message=True)
        ),
    )
    refresh_cycle(engine)
    refresh_cycle(engine)
    assert [method for method in attempts if not method.startswith("delete")] == ["editMessageText"]
    with Session(engine) as session:
        assert session.get(TelegramView, TG_ID) is None


def test_navigation_discards_a_failed_refresh_for_the_previous_day(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    arrow = button(sent, "День →")
    web_manual(client)
    monkeypatch.setattr(telegram_bot, "telegram_api", lambda *args: TelegramReply(False))
    refresh_cycle(engine)
    with Session(engine) as session:
        assert session.exec(select(TelegramOutbox)).one().is_refresh
    sent.clear()
    monkeypatch.setattr(
        telegram_bot,
        "telegram_api",
        lambda method, payload: (
            sent.append((method, payload)) or TelegramReply(True, message_id=42)
        ),
    )
    send(client, callback(arrow), 2)
    with Session(engine) as session:
        assert session.exec(select(TelegramOutbox)).first() is None
        assert (
            session.get(TelegramView, TG_ID).context["day"]
            == (NOW.date() + timedelta(days=1)).isoformat()
        )
    sent.clear()
    refresh_cycle(engine)
    assert all(method.startswith("delete") for method, _ in sent)


def test_background_refresh_skips_busy_chat_without_blocking_website(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    sent.clear()
    with ThreadPoolExecutor(max_workers=1) as pool, chat_lock(engine, TG_ID):
        web_manual(client)
        pool.submit(refresh_cycle, engine).result(timeout=5)
        assert sent == []
    refresh_cycle(engine)
    assert len(sent) == 1


def test_buttons_are_renewed_without_edits_and_rebuilt_when_missing(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    with Session(engine) as session:
        token = session.get(TelegramView, TG_ID).payload["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"
        ]
        session.get(TelegramAction, token).expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    sent.clear()
    refresh_cycle(engine)
    assert sent == []
    with Session(engine) as session:
        action = session.get(TelegramAction, token)
        assert (
            action.expires_at.replace(tzinfo=action.expires_at.tzinfo or UTC) - utc_now()
        ).total_seconds() > 600
        session.delete(action)
        session.commit()
    refresh_cycle(engine)
    assert len(sent) == 1


def test_refresh_stops_when_admin_access_is_revoked(tg, engine, monkeypatch):
    client, sent, _ = tg
    google_customer(client, engine, monkeypatch)
    send(client, message("/start"))
    monkeypatch.setattr(settings, "admin_google_email", "someone@example.com")
    sent.clear()
    refresh_cycle(engine)
    assert sent == []
    with Session(engine) as session:
        assert session.get(TelegramView, TG_ID) is None


def test_empty_schedule_auto_reopens_when_working_hours_change(tg, engine, monkeypatch):
    client, sent, service_id = tg
    google_customer(client, engine, monkeypatch)
    with Session(engine) as session:
        session.add(ServiceSchedule(service_id=service_id, intervals=[]))
        session.commit()
    send(client, message("/start"))
    with Session(engine) as session:
        assert session.get(TelegramView, TG_ID) is not None
        session.get(ServiceSchedule, service_id).intervals = [(14 * 60, 21 * 60)]
        session.commit()
    sent.clear()
    refresh_cycle(engine)
    assert len(sent) == 1 and "20:30" in str(sent[0][1])


def test_refresh_lifespan_starts_and_stops_worker(tg, engine, monkeypatch):
    from app import database, telegram_refresh

    calls = []
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(telegram_refresh, "REFRESH_SECONDS", 0.01)
    monkeypatch.setattr(telegram_refresh, "refresh_cycle", lambda e: calls.append(e))

    async def run():
        async with telegram_refresh.lifespan(main.app):
            await asyncio.sleep(0.06)
            assert calls and all(e is engine for e in calls)
        count = len(calls)
        await asyncio.sleep(0.03)
        assert len(calls) == count

    asyncio.run(run())


@pytest.mark.parametrize(
    "response, expected",
    [
        ({"ok": True, "result": {"message_id": 123}}, (True, 123, False, False, 10)),
        (
            {"ok": False, "error_code": 400, "description": "Bad Request: message is not modified"},
            (True, 42, False, False, 10),
        ),
        (
            {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message to edit not found",
            },
            (False, None, True, True, 10),
        ),
        ({"ok": False, "error_code": 403}, (False, None, True, False, 10)),
        (
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 30}},
            (False, None, False, False, 30),
        ),
    ],
)
def test_telegram_transport_classifies_delivery_and_retry(tg, monkeypatch, response, expected):
    import httpx

    from app import telegram_transport

    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    monkeypatch.setattr(
        telegram_transport.httpx,
        "Client",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    reply = telegram_transport.telegram_api(
        "editMessageText", {"message_id": 42, "chat_id": TG_ID, "text": "Test"}
    )
    assert (
        reply.ok,
        reply.message_id,
        reply.terminal,
        reply.missing_message,
        reply.retry_after,
    ) == expected


@pytest.fixture
def tidy_bot(tg, engine, monkeypatch):
    client, _sent, service_id = tg
    google_customer(client, engine, monkeypatch)
    calls = []
    last_id = 1000

    def api(method, payload):
        nonlocal last_id
        calls.append((method, payload))
        if method == "sendMessage":
            last_id += 1
            message_id = last_id
        elif method == "editMessageText":
            message_id = payload["message_id"]
        else:
            message_id = None
        return TelegramReply(
            True, message_id=message_id, message_date=utc_now() if message_id else None
        )

    monkeypatch.setattr(telegram_bot, "telegram_api", api)
    return client, calls, service_id


def screen_id(engine):
    with Session(engine) as session:
        return session.get(TelegramChat, TG_ID).message_id


def deleted_ids(calls):
    return {
        message_id
        for method, payload in calls
        if method == "deleteMessages"
        for message_id in payload["message_ids"]
    }


def test_chat_cleanup_keeps_unsaved_input_and_current_screen(tidy_bot, engine):
    client, calls, _ = tidy_bot
    send(client, message("/start"), 1)
    send(client, callback(button(calls, "10:00 ＋"), message_id=screen_id(engine)), 2)
    send(client, message("0981234567"), 3)
    refresh_cycle(engine)
    assert 10003 not in deleted_ids(calls)
    send(client, message("Пацієнт"), 4)
    active = screen_id(engine)
    refresh_cycle(engine)
    assert {10001, 10003, 10004}.issubset(deleted_ids(calls))
    assert active not in deleted_ids(calls)
    with Session(engine) as session:
        assert session.exec(select(Appointment)).one().phone == "0981234567"
        assert {row.message_id for row in session.exec(select(TelegramMessage))} == {active}
        assert session.get(TelegramView, TG_ID).message_id == active
    before = len(deleted_ids(calls))
    refresh_cycle(engine)
    assert len(deleted_ids(calls)) == before


def test_failed_save_preserves_input_until_explicit_cancel(tidy_bot, engine):
    client, calls, _ = tidy_bot
    send(client, message("/start"), 1)
    send(client, callback(button(calls, "10:00 ＋"), message_id=screen_id(engine)), 2)
    send(client, message("0981234567"), 3)
    web_manual(client)
    send(client, message("Name"), 4)
    refresh_cycle(engine)
    assert {10003, 10004}.isdisjoint(deleted_ids(calls))
    with Session(engine) as session:
        assert session.get(TelegramState, TG_ID).payload["mode"] == "name"
        assert session.get(TelegramMessage, (TG_ID, 10003)).pending
    send(client, message("/cancel"), 5)
    refresh_cycle(engine)
    assert {10003, 10004}.issubset(deleted_ids(calls))
    with Session(engine) as session:
        assert len(session.exec(select(Appointment)).all()) == 1


def test_failed_new_screen_is_not_cleaned_before_delivery(tidy_bot, engine, monkeypatch):
    client, calls, _ = tidy_bot
    send(client, message("/start"), 1)
    old = screen_id(engine)
    original = telegram_bot.telegram_api
    monkeypatch.setattr(telegram_bot, "telegram_api", lambda *args: TelegramReply(False))
    assert send(client, message("/records"), 2).status_code == 503
    refresh_cycle(engine)
    assert old not in deleted_ids(calls)
    assert 10002 not in deleted_ids(calls)
    with Session(engine) as session:
        for row in session.exec(select(TelegramOutbox)):
            row.not_before = utc_now() - timedelta(seconds=1)
        session.commit()
    monkeypatch.setattr(telegram_bot, "telegram_api", original)
    refresh_cycle(engine)
    assert old in deleted_ids(calls)
    assert screen_id(engine) not in deleted_ids(calls)


def test_cleanup_backoff_preserves_calendar_updates(tidy_bot, engine, monkeypatch):
    client, calls, _ = tidy_bot
    send(client, message("/start"), 1)
    original = telegram_bot.telegram_api
    deletes = []

    def limit_delete(method, payload):
        if method == "deleteMessages":
            deletes.append(payload)
            return TelegramReply(False, error_code=429, retry_after=30)
        return original(method, payload)

    monkeypatch.setattr(telegram_bot, "telegram_api", limit_delete)
    refresh_cycle(engine)
    web_manual(client)
    refresh_cycle(engine)
    assert len(deletes) == 1
    assert any(
        "Refresh patient" in str(payload)
        for method, payload in calls
        if method == "editMessageText"
    )
    with Session(engine) as session:
        session.get(TelegramChat, TG_ID).cleanup_after = utc_now() - timedelta(seconds=1)
        session.commit()
    monkeypatch.setattr(telegram_bot, "telegram_api", original)
    refresh_cycle(engine)
    assert 10001 in deleted_ids(calls)
    assert screen_id(engine) not in deleted_ids(calls)


def test_rejected_replacement_does_not_delete_the_previous_screen(tidy_bot, engine, monkeypatch):
    client, calls, _ = tidy_bot
    send(client, message("/start"), 1)
    old = screen_id(engine)
    original = telegram_bot.telegram_api
    monkeypatch.setattr(
        telegram_bot,
        "telegram_api",
        lambda *args: TelegramReply(False, terminal=True, error_code=400),
    )
    send(client, message("/records"), 2)
    monkeypatch.setattr(telegram_bot, "telegram_api", original)
    refresh_cycle(engine)
    assert old not in deleted_ids(calls)
    assert 10002 not in deleted_ids(calls)
    send(client, message("/start"), 3)
    refresh_cycle(engine)
    assert {old, 10002}.issubset(deleted_ids(calls))
    assert screen_id(engine) not in deleted_ids(calls)


def test_cleanup_respects_chat_scope_and_telegram_age_limit(tg, engine, monkeypatch):
    _client, _sent, _ = tg
    calls = []
    monkeypatch.setattr(
        telegram_bot,
        "telegram_api",
        lambda method, payload: calls.append((method, payload)) or TelegramReply(True),
    )
    with Session(engine) as session:
        session.add(TelegramChat(telegram_id=TG_ID, message_id=999))
        session.add(TelegramChat(telegram_id=TG_ID + 1, message_id=888))
        remember(session, TG_ID, 1, is_ui=True, created_at=utc_now() - timedelta(hours=49))
        remember(session, TG_ID, 2, is_ui=False, pending=True)
        remember(session, TG_ID, 3, is_ui=False)
        remember(session, TG_ID, 999, is_ui=True)
        remember(session, TG_ID + 1, 4, is_ui=False)
        session.commit()
        cleanup_chat(session, TG_ID)
        assert calls == [("deleteMessages", {"chat_id": TG_ID, "message_ids": [3]})]
        assert session.get(TelegramMessage, (TG_ID, 1)) is None
        assert session.get(TelegramMessage, (TG_ID, 2)) is not None
        assert session.get(TelegramMessage, (TG_ID, 999)) is not None
        assert session.get(TelegramMessage, (TG_ID + 1, 4)) is not None


def test_unknown_text_and_media_are_not_auto_deleted(tidy_bot, engine):
    client, calls, _ = tidy_bot
    send(client, message("/start"), 1)
    send(client, message("Особиста нотатка"), 2)
    photo = message("")
    photo["message"].pop("text")
    photo["message"]["photo"] = [{"file_id": "not-a-real-file"}]
    send(client, photo, 3)
    refresh_cycle(engine)
    assert {10002, 10003}.isdisjoint(deleted_ids(calls))
    with Session(engine) as session:
        assert session.get(TelegramMessage, (TG_ID, 10002)) is None
        assert session.get(TelegramMessage, (TG_ID, 10003)) is None


def test_individual_undeletable_message_does_not_strand_cleanup(tg, engine, monkeypatch):
    _client, _sent, _ = tg
    calls = []

    def api(method, payload):
        calls.append((method, payload))
        if method == "deleteMessages" or payload.get("message_id") == 1:
            return TelegramReply(False, error_code=400, terminal=True)
        return TelegramReply(True)

    monkeypatch.setattr(telegram_bot, "telegram_api", api)
    with Session(engine) as session:
        session.add(TelegramChat(telegram_id=TG_ID, message_id=999))
        remember(session, TG_ID, 1, is_ui=True)
        remember(session, TG_ID, 2, is_ui=False)
        session.commit()
        cleanup_chat(session, TG_ID)
        assert session.exec(select(TelegramMessage)).first() is None
    assert [method for method, _ in calls] == ["deleteMessages", "deleteMessage", "deleteMessage"]
