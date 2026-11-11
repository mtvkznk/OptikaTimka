import re
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import ASSET_VERSION, KYIV_TZ, booking_window, current_date


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_homepage_renders_booking_form(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Запис на прийом" in response.text
    assert "data-appointment-form" in response.text
    assert "/api/appointments" in response.text
    assert "Перевірка зору" in response.text
    assert "data-service-picker" in response.text
    assert "data-service-option" in response.text
    assert "500 грн" in response.text
    assert "30 хв" in response.text
    assert "Оберіть дату" in response.text
    assert "Коли відкриваються нові дати?" in response.text
    assert "Щодня об 11:00 за Києвом" in response.text
    assert "data-calendar-next" in response.text
    assert "calendar-board" in response.text
    assert "calendar-grid" in response.text
    assert "Оберіть послугу й дату, щоб побачити вільний час." in response.text
    assert "data-time-slots" in response.text
    assert 'name="preferred_date"' in response.text
    assert 'name="preferred_time"' in response.text
    assert 'name="service"' in response.text
    assert 'inputmode="tel"' in response.text
    assert 'href="/cabinet"' in response.text
    assert "booking.js" in response.text
    assert ASSET_VERSION in response.text
    assert "Наприклад" not in response.text


def test_booking_window_releases_new_date_at_11_kyiv() -> None:
    before_release = datetime(2026, 9, 16, 10, 59, tzinfo=KYIV_TZ)
    after_release = datetime(2026, 9, 16, 11, 0, tzinfo=KYIV_TZ)

    assert booking_window(before_release) == (
        date(2026, 9, 16),
        date(2026, 10, 15),
    )
    assert booking_window(after_release) == (
        date(2026, 9, 16),
        date(2026, 10, 16),
    )


def test_api_status_describes_backend_stack(client: TestClient) -> None:
    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["stack"] == ["FastAPI", "SQLModel", "Alembic", "PostgreSQL"]


def test_admin_dashboard_renders_controls(client: TestClient) -> None:
    response = client.get("/admin")

    assert response.status_code == 200
    assert "Адмінпанель" in response.text
    assert "Останні записи" in response.text
    assert "/admin/appointments" in response.text
    assert "Доступні години" in response.text
    assert "Послуги" in response.text
    assert "Закриті дати" in response.text


def test_create_and_list_product(client: TestClient) -> None:
    create_response = client.post(
        "/api/products",
        json={
            "category": "frames",
            "name": "Classic frame",
            "description": "Lightweight everyday frame.",
            "price_cents": 120000,
            "currency": "UAH",
            "sort_order": 1,
        },
    )

    assert create_response.status_code == 201
    assert create_response.json()["name"] == "Classic frame"

    list_response = client.get("/api/products")

    assert list_response.status_code == 200
    assert list_response.json()[0]["name"] == "Classic frame"


def test_create_appointment(client: TestClient) -> None:
    preferred_date = current_date() + timedelta(days=1)
    response = client.post(
        "/api/appointments",
        json={
            "name": "Ivan Petrenko",
            "phone": "+380980000000",
            "email": "ivan@example.com",
            "service": "Перевірка зору",
            "preferred_date": preferred_date.isoformat(),
            "preferred_time": "10:00:00",
            "message": "Need a consultation.",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "new"


def test_create_appointment_rejects_date_outside_booking_window(client: TestClient) -> None:
    _, window_end = booking_window()
    response = client.post(
        "/api/appointments",
        json={
            "name": "Too Late Client",
            "phone": "+380990000000",
            "email": None,
            "service": "Перевірка зору",
            "preferred_date": (window_end + timedelta(days=1)).isoformat(),
            "preferred_time": "10:00:00",
            "message": "",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Запис доступний не більше ніж на 30 днів вперед."


def test_admin_can_add_service_that_homepage_uses(client: TestClient) -> None:
    response = client.post(
        "/admin/services",
        data={
            "name": "Ремонт окулярів",
            "duration_minutes": "20",
            "price_uah": "300",
            "sort_order": "10",
            "is_active": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    homepage = client.get("/")
    assert homepage.status_code == 200
    assert "Ремонт окулярів" in homepage.text


def test_admin_can_add_time_slot_that_appears_in_availability(client: TestClient) -> None:
    response = client.post(
        "/admin/time-slots",
        data={
            "start_time": "17:00",
            "end_time": "18:00",
            "sort_order": "20",
            "is_active": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    tomorrow = current_date() + timedelta(days=1)
    availability = client.get("/api/availability", params={"preferred_date": tomorrow.isoformat()})
    assert availability.status_code == 200
    assert any(
        slot["start_time"] == "17:00:00" and slot["end_time"] == "18:00:00"
        for slot in availability.json()
    )


def test_closed_date_is_not_available_on_booking_form(client: TestClient) -> None:
    closed_on = current_date() + timedelta(days=3)
    close_response = client.post(
        "/admin/closed-dates",
        data={
            "closed_on": closed_on.isoformat(),
            "reason": "Санітарний день",
        },
        follow_redirects=False,
    )

    assert close_response.status_code == 303

    homepage = client.get("/")
    assert homepage.status_code == 200
    assert f'value="{closed_on.isoformat()}"' not in homepage.text


def test_closed_date_blocks_appointment_booking(client: TestClient) -> None:
    closed_on = current_date() + timedelta(days=5)
    close_response = client.post(
        "/admin/closed-dates",
        data={
            "closed_on": closed_on.isoformat(),
            "reason": "Санітарний день",
        },
        follow_redirects=False,
    )

    assert close_response.status_code == 303

    response = client.post(
        "/api/appointments",
        json={
            "name": "Olena Kovalenko",
            "phone": "+380970000000",
            "email": None,
            "service": "Перевірка зору",
            "preferred_date": closed_on.isoformat(),
            "preferred_time": "09:00:00",
            "message": "",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "На цю дату запис закритий."


def test_admin_appointments_page_renders_all_appointments(client: TestClient) -> None:
    preferred_date = current_date() + timedelta(days=6)
    create_response = client.post(
        "/api/appointments",
        json={
            "name": "All Records Client",
            "phone": "+380500000000",
            "email": None,
            "service": "Перевірка зору",
            "preferred_date": preferred_date.isoformat(),
            "preferred_time": "09:00:00",
            "message": "",
        },
    )

    assert create_response.status_code == 201

    response = client.get("/admin/appointments")
    assert response.status_code == 200
    assert "Усі записи" in response.text
    assert "All Records Client" in response.text


def test_admin_can_update_appointment_status(client: TestClient) -> None:
    preferred_date = current_date() + timedelta(days=7)
    appointment_response = client.post(
        "/api/appointments",
        json={
            "name": "Serhii Melnyk",
            "phone": "+380630000000",
            "email": None,
            "service": "Перевірка зору",
            "preferred_date": preferred_date.isoformat(),
            "preferred_time": "09:00:00",
            "message": "",
        },
    )
    appointment_id = appointment_response.json()["id"]

    update_response = client.post(
        f"/admin/appointments/{appointment_id}/status",
        data={"status": "confirmed"},
        follow_redirects=False,
    )

    assert update_response.status_code == 303

    admin_page = client.get("/admin")
    assert admin_page.status_code == 200
    assert "Підтверджено" in admin_page.text


def test_admin_can_reorder_services(client: TestClient) -> None:
    services = client.get("/api/booking-services").json()
    reversed_ids = [item["id"] for item in reversed(services)]

    response = client.post("/admin/reorder/services", json={"ids": reversed_ids})

    assert response.status_code == 200

    reordered_services = client.get("/api/booking-services").json()
    assert [item["id"] for item in reordered_services] == reversed_ids
    stale = client.post("/admin/reorder/services", json={"ids": reversed_ids[1:]})
    assert stale.status_code == 409
    assert client.get("/api/booking-services").json() == reordered_services


def test_admin_can_reorder_time_slots(client: TestClient) -> None:
    slots = client.get("/api/time-slots").json()
    reversed_ids = [item["id"] for item in reversed(slots)]

    response = client.post("/admin/reorder/time-slots", json={"ids": reversed_ids})

    assert response.status_code == 200

    reordered_slots = client.get("/api/time-slots").json()
    assert [item["id"] for item in reordered_slots] == reversed_ids


def test_editing_seeded_service_and_time_keeps_only_the_edited_versions(client: TestClient) -> None:
    services = client.get("/api/booking-services").json()
    slots = client.get("/api/time-slots").json()
    service, slot = services[0], slots[0]
    assert client.post(
        f"/admin/services/{service['id']}",
        data={"name": "Первинна консультація", "is_active": "on"},
        follow_redirects=False,
    ).status_code == 303
    assert client.post(
        f"/admin/time-slots/{slot['id']}",
        data={"start_time": "09:10", "end_time": slot["end_time"], "is_active": "on"},
        follow_redirects=False,
    ).status_code == 303

    edited_services = client.get("/api/booking-services").json()
    edited_slots = client.get("/api/time-slots").json()
    assert [item["id"] for item in edited_services] == [item["id"] for item in services]
    assert [item["id"] for item in edited_slots] == [item["id"] for item in slots]
    assert edited_services[0]["name"] == "Первинна консультація"
    assert edited_slots[0]["start_time"] == "09:10:00"


def test_cabinet_renders_passwordless_login_options(client: TestClient) -> None:
    response = client.get("/cabinet")

    assert response.status_code == 200
    assert "Особистий кабінет" in response.text
    assert "Вхід телефоном" in response.text
    assert 'href="/auth/google"' not in response.text
    assert 'action="/auth/phone/request"' in response.text
    assert 'action="/auth/phone/verify"' not in response.text


def test_phone_code_login_shows_matching_appointments(client: TestClient) -> None:
    phone = "+48123456789"
    preferred_date = current_date() + timedelta(days=8)
    appointment_response = client.post(
        "/api/appointments",
        json={
            "name": "Phone Cabinet Client",
            "phone": phone,
            "email": None,
            "service": "Перевірка зору",
            "preferred_date": preferred_date.isoformat(),
            "preferred_time": "09:00:00",
            "message": "",
        },
    )
    assert appointment_response.status_code == 201

    code_response = client.post("/auth/phone/request", data={"phone": phone})
    assert code_response.status_code == 200
    assert 'action="/auth/phone/verify"' in code_response.text
    code_match = re.search(r"Тестовий код:\s*<strong>(\d{6})</strong>", code_response.text)
    assert code_match is not None

    login_response = client.post(
        "/auth/phone/verify",
        data={"phone": phone, "code": code_match.group(1)},
        follow_redirects=False,
    )
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/cabinet"

    cabinet_response = client.get("/cabinet")
    assert cabinet_response.status_code == 200
    assert "Phone Cabinet Client" in cabinet_response.text
    assert "Перевірка зору" in cabinet_response.text
    assert "09:00" in cabinet_response.text

    logout_response = client.post("/auth/logout", follow_redirects=False)
    assert logout_response.status_code == 303


def test_google_login_redirects_to_cabinet_when_not_configured(client: TestClient) -> None:
    response = client.get("/auth/google", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/cabinet?auth_error=google_not_configured"
