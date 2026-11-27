import json
import re
from base64 import b64encode
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from pytest import MonkeyPatch, mark
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app import main
from app.admin_changes import service_schedule_version
from app.config import settings
from app.main import current_date
from app.models import (
    AdminAuditEvent,
    Appointment,
    AppointmentStatus,
    BookingBlock,
    BookingService,
    CatalogCategory,
    ClosedDate,
    Customer,
    ServiceClosedDate,
    ServiceSchedule,
    ServiceWeekdaySchedule,
)


def confirm_schedule_change(client: TestClient, draft: dict) -> None:
    response = client.post("/admin/changes", json=draft)
    assert response.status_code == 409
    warning = response.json()["detail"]
    assert warning["kind"] == "schedule_review" and warning["total"] == 1
    assert client.post("/admin/changes", json={
        **draft, "schedule_acknowledgement": warning["token"],
    }).status_code == 200


def test_doctor_notebook_orders_the_day_without_showing_other_clients(
    client: TestClient, app_db: Engine, monkeypatch: MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 17, 18, 55, tzinfo=main.KYIV_TZ)
    monkeypatch.setattr(main, "kyiv_now", lambda: now)
    with Session(app_db) as session:
        service = BookingService(name="Diary service")
        other_service = BookingService(name="Other service")
        session.add_all([service, other_service])
        session.flush()
        service_id = service.id
        for name, day, starts, status in (
            ("Later in the diary", date(2026, 12, 1), time(14, 30), AppointmentStatus.new),
            ("Earlier in the diary", date(2026, 12, 1), time(11, 10), AppointmentStatus.confirmed),
            ("Canceled visit", date(2026, 12, 1), time(10), AppointmentStatus.canceled),
            ("Visit on another day", date(2026, 12, 2), time(10), AppointmentStatus.confirmed),
        ):
            session.add(Appointment(
                name=name, phone="+380980000001", service=service.name, service_id=service_id,
                preferred_date=day, preferred_time=starts, status=status,
            ))
        session.add(Appointment(
            name="Visit for another service", phone="+380980000001",
            service=other_service.name, service_id=other_service.id,
            preferred_date=date(2026, 12, 1), preferred_time=time(16),
        ))
        # Changing the current service name must preserve historical diary membership.
        service.name = "Updated diary service"
        session.commit()

    monkeypatch.setattr(settings, "doctor_diary_service_id", service_id)
    response = client.get("/admin", params={"day": "2026-12-01"})
    assert response.status_code == 200
    agenda = response.text.split('id="appointment-diary"', 1)[1].split('data-admin-view="schedule"', 1)[0]
    assert agenda.index("Earlier in the diary") < agenda.index("Later in the diary")
    assert "Canceled visit" not in agenda and "Visit on another day" not in agenda
    assert "Visit for another service" not in agenda
    assert ">Зайнято<" in agenda and "іншою послугою" not in agenda
    assert "Прийом триває" in agenda
    assert "Updated diary service" in agenda
    assert 'data-notebook-start="11:00"' not in agenda  # The ten-minute gap is too short.
    assert 'data-notebook-start="09:00"' in agenda
    manual_fields = agenda.split('id="manual-appointment"', 1)[1].split("</form>", 1)[0]
    assert 'name="service_id"' not in manual_fields and 'name="end_time"' not in manual_fields
    assert 'day=2026-11-30#appointment-diary' in agenda
    assert 'day=2026-12-02#appointment-diary' in agenda


@mark.parametrize(("days_ahead", "expected_status"), [
    (0, AppointmentStatus.confirmed),
    (2, AppointmentStatus.new),
])
def test_phone_only_notebook_appointment_uses_doctor_service_duration(
    client: TestClient, anonymous_client: TestClient, app_db: Engine, monkeypatch: MonkeyPatch,
    days_ahead: int, expected_status: AppointmentStatus,
) -> None:
    now = datetime(2026, 11, 18, 8, tzinfo=main.KYIV_TZ)
    monkeypatch.setattr(main, "kyiv_now", lambda: now)
    day = (now.date() + timedelta(days=days_ahead)).isoformat()
    services = anonymous_client.get("/api/booking-services").json()
    service, other_service = services[:2]
    with Session(app_db) as session:
        doctor = session.get(BookingService, service["id"])
        doctor.duration_minutes = 35
        session.commit()
    request = {
        "phone": "067 123 45 67",
        "preferred_date": day, "preferred_time": "10:10",
    }
    endpoint = "/admin/appointments/manual"
    assert anonymous_client.post(endpoint, data=request).status_code == 401
    result = client.post(endpoint, data={
        **request, "service_id": str(other_service["id"]), "end_time": "11:47",
    }, follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == f"/admin?day={day}#appointment-diary"
    assert client.post(endpoint, data={**request, "preferred_time": "10:35"}).status_code == 409
    assert client.post(endpoint, data={**request, "preferred_time": "12:35"}).status_code == 409
    assert client.post(endpoint, data={**request, "phone": ""}).status_code == 422

    slots = anonymous_client.get("/api/availability", params={
        "preferred_date": day, "service": other_service["name"],
    }).json()
    assert slots and all(
        slot["end_time"] <= "10:10:00" or slot["start_time"] >= "10:45:00" for slot in slots
    )
    notebook = client.get("/admin", params={"day": day}).text
    assert f'class="admin-notebook-visit" data-status="{expected_status.value}"' in notebook
    with Session(app_db) as session:
        appointment = session.exec(select(Appointment)).one()
        assert not appointment.name
        assert appointment.phone == request["phone"]
        assert appointment.phone_normalized == "+380671234567"
        assert appointment.status == expected_status and appointment.customer_id is None
        assert appointment.duration_minutes == 35 and appointment.service_id == service["id"]


def test_notebook_contact_edit_preserves_the_appointment_and_rejects_stale_versions(
    client: TestClient, anonymous_client: TestClient, app_db: Engine,
) -> None:
    doctor, other = client.get("/api/booking-services").json()[:2]
    day = date(2026, 12, 1)
    with Session(app_db) as session:
        customer = Customer(google_sub="appointment-owner")
        session.add(customer)
        session.flush()
        owner_id = customer.id
        visit = Appointment(
            name="Old test name", phone="067 000 00 01", phone_normalized="+380670000001",
            service=doctor["name"], service_id=doctor["id"],
            customer_id=owner_id, preferred_date=day, preferred_time=time(10),
            status=AppointmentStatus.confirmed, duration_minutes=45, message="Test comment",
        )
        other_visit = Appointment(name="Other service", phone="", service=other["name"], service_id=other["id"])
        session.add_all([visit, other_visit])
        session.commit()
        appointment_id, version = visit.id, visit.updated_at.isoformat()
        other_id, other_version = other_visit.id, other_visit.updated_at.isoformat()

    endpoint = f"/admin/appointments/{appointment_id}/contact"
    form = {"name": " Updated test name ", "phone": " 067 000 00 02 ", "version": version}
    assert anonymous_client.post(endpoint, data=form).status_code == 401
    assert client.post(endpoint, data={**form, "name": "", "phone": " "}).status_code == 422
    assert client.post(f"/admin/appointments/{other_id}/contact", data={
        **form, "version": other_version,
    }).status_code == 404
    saved = client.post(endpoint, data=form, headers={"Accept": "application/json"})
    assert saved.status_code == 200
    assert saved.json()["version"] != version
    assert saved.json()["redirect"] == f"/admin?day={day}#notebook-slot-1000"
    assert client.post(endpoint, data={**form, "phone": "067 000 00 03"}).status_code == 409

    assert 'href="tel:+380670000002"' in client.get(saved.json()["redirect"]).text
    assert client.post(endpoint, data={**form, "version": saved.json()["version"]}, headers={
        "Accept": "application/json",
    }).json()["version"] == saved.json()["version"]
    with Session(app_db) as session:
        visit = session.get(Appointment, appointment_id)
        assert (visit.name, visit.phone, visit.phone_normalized) == (
            "Updated test name", "067 000 00 02", "+380670000002",
        )
        assert (visit.customer_id, visit.service_id) == (owner_id, doctor["id"])
        assert (visit.preferred_date, visit.preferred_time, visit.duration_minutes) == (day, time(10), 45)
        assert (visit.status, visit.message) == (AppointmentStatus.confirmed, "Test comment")
        event = session.exec(select(AdminAuditEvent)).one()
        assert event.action == "appointment_contact_updated"
        assert event.detail.startswith(f"Прийом №{appointment_id} · ")
        assert "Updated test name" not in event.detail and "067 000 00 02" not in event.detail


def test_notebook_closes_and_reopens_free_time_without_overlapping_bookings(
    client: TestClient, anonymous_client: TestClient, app_db: Engine,
) -> None:
    doctor, other = client.get("/api/booking-services").json()[:2]
    day = current_date() + timedelta(days=2)
    with Session(app_db) as session:
        session.get(BookingService, other["id"]).duration_minutes = 60
        booked = Appointment(
            name="Existing visit", phone="+380980000001", service=doctor["name"],
            service_id=doctor["id"], preferred_date=day, preferred_time=time(10, 15),
            duration_minutes=30,
        )
        canceled = Appointment(
            name="Canceled visit", phone="+380980000002", service=doctor["name"],
            service_id=doctor["id"], preferred_date=day, preferred_time=time(9, 30),
            duration_minutes=30, status=AppointmentStatus.canceled,
        )
        session.add_all([booked, canceled])
        session.commit()
        canceled_id = canceled.id

    notebook = f"/admin?day={day.isoformat()}"
    endpoint = "/admin/appointments/slot-block"
    form = {
        "preferred_date": day.isoformat(), "preferred_time": "09:30",
        "service_id": str(doctor["id"]), "block_action": "close", "slot_duration": "30",
    }

    def starts(service: dict) -> list[str]:
        response = anonymous_client.get("/api/availability", params={
            "preferred_date": day, "service": service["name"],
        })
        assert response.status_code == 200
        return [slot["start_time"] for slot in response.json()]

    html = client.get(notebook).text
    assert 'aria-label="Закрити час 09:30"' in html
    assert 'aria-label="Закрити час 10:00"' not in html
    assert "09:30:00" in starts(doctor) and "09:00:00" in starts(other)
    assert anonymous_client.post(endpoint, data=form).status_code == 401
    assert client.post(endpoint, data={**form, "preferred_time": "10:00"}).status_code == 409
    assert client.post(endpoint, data={**form, "slot_duration": "60"}).status_code == 409
    assert client.post(endpoint, data={**form, "service_id": str(other["id"])}).status_code == 409
    response = client.post(endpoint, data=form, headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.json()["redirect"] == f"{notebook}#notebook-slot-0930"
    assert client.post(endpoint, data=form, follow_redirects=False).status_code == 303
    assert "09:30:00" not in starts(doctor) and "09:00:00" in starts(other)
    html = client.get(notebook).text
    assert 'aria-label="Відкрити час 09:30"' in html and "Existing visit" in html
    assert 'aria-label="Додати запис на 09:30"' not in html
    booking = {
        "preferred_date": day.isoformat(), "preferred_time": "09:30", "phone": "+380501234567",
    }
    assert client.post("/admin/appointments/manual", data=booking).status_code == 409
    assert client.post("/api/appointments", json={
        **booking, "name": "Test client", "service": doctor["name"],
    }).status_code == 409
    assert client.post(f"/admin/appointments/{canceled_id}/status", data={
        "status": "confirmed",
    }).status_code == 409

    alternative = client.post("/api/appointments", json={
        **booking, "preferred_time": "09:00", "name": "Other client", "service": other["name"],
    })
    assert alternative.status_code == 201
    assert 'aria-label="Відкрити час 09:30"' in client.get(notebook).text
    assert client.post(endpoint, data={**form, "block_action": "open"}, follow_redirects=False).status_code == 303
    assert "09:30:00" not in starts(doctor)
    alternative_id = alternative.json()["id"]
    assert client.post(f"/admin/appointments/{alternative_id}/status", data={
        "status": "canceled",
    }, follow_redirects=False).status_code == 303
    assert "09:30:00" in starts(doctor) and "09:00:00" in starts(other)


def test_notebook_move_uses_original_duration_and_frees_the_old_hour(
    client: TestClient, anonymous_client: TestClient, app_db: Engine,
) -> None:
    doctor, other = client.get("/api/booking-services").json()[:2]
    today = current_date()
    original = today + timedelta(days=2)
    target = original + timedelta(days=1)
    closed = target + timedelta(days=1)
    with Session(app_db) as session:
        visit = Appointment(
            name="Move this patient", phone="+380980000001", service=doctor["name"],
            service_id=doctor["id"], preferred_date=original, preferred_time=time(10),
            duration_minutes=45, status=AppointmentStatus.confirmed,
        )
        session.add_all([
            visit,
            Appointment(
                name="Other visit", phone="+380980000002", service=other["name"],
                service_id=other["id"], preferred_date=target, preferred_time=time(10, 45),
                duration_minutes=15,
            ),
            BookingBlock(service_id=doctor["id"], day=target, start_minute=540, end_minute=585),
            ClosedDate(closed_on=closed),
        ])
        session.commit()
        visit_id = visit.id
        version = visit.updated_at.isoformat()

    def starts(day: date) -> list[str]:
        response = client.get("/api/availability", params={
            "preferred_date": day.isoformat(), "service": doctor["name"],
        })
        assert response.status_code == 200
        return [option["start_time"] for option in response.json()]

    assert "10:00:00" not in starts(original)
    html = client.get("/admin", params={"day": target, "move": visit_id}).text
    assert 'move_version=' in html
    assert 'aria-label="Підтвердити перенесення на 09:45"' in html
    assert 'aria-label="Підтвердити перенесення на 09:00"' not in html
    assert 'aria-label="Підтвердити перенесення на 10:30"' not in html
    assert "data-notebook-start=" not in html

    endpoint = f"/admin/appointments/{visit_id}/move"
    form = {"preferred_date": target.isoformat(), "preferred_time": "09:45", "version": version}
    assert anonymous_client.post(endpoint, data=form).status_code == 401
    assert client.post(endpoint, data={**form, "preferred_time": "10:30"}).status_code == 409
    assert client.post(endpoint, data={**form, "preferred_time": "09:00"}).status_code == 409
    assert client.post(endpoint, data={**form, "preferred_date": closed.isoformat()}).status_code == 409
    assert client.post(endpoint, data={**form, "preferred_date": today.isoformat(), "preferred_time": "00:00"}).status_code == 409

    moved = client.post(endpoint, data=form, headers={"Accept": "application/json"})
    assert moved.status_code == 200
    assert moved.json()["redirect"] == f"/admin?day={target.isoformat()}#notebook-slot-0945"
    assert client.post(endpoint, data=form).status_code == 409
    assert "10:00:00" in starts(original) and "09:30:00" not in starts(target)
    with Session(app_db) as session:
        moved_visit = session.get(Appointment, visit_id)
        assert (moved_visit.preferred_date, moved_visit.preferred_time) == (target, time(9, 45))
        assert moved_visit.updated_at.isoformat() == moved.json()["version"]
        assert moved_visit.status == AppointmentStatus.confirmed and moved_visit.duration_minutes == 45
        assert moved_visit.phone == "+380980000001" and moved_visit.service_id == doctor["id"]
        audit = session.exec(select(AdminAuditEvent)).one()
        assert audit.action == "appointment_moved" and audit.actor == "admin"
        assert "Move this patient" not in audit.detail and moved_visit.phone not in audit.detail


def test_notebook_skips_days_closed_for_the_doctor(
    client: TestClient, app_db: Engine, monkeypatch: MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 19, 9, tzinfo=main.KYIV_TZ)
    monkeypatch.setattr(main, "kyiv_now", lambda: now)
    doctor, other = client.get("/api/booking-services").json()[:2]
    with Session(app_db) as session:
        service = session.get(BookingService, doctor["id"])
        service.weekdays_mask = 0b111  # Monday to Wednesday.
        session.add_all([
            ServiceClosedDate(service_id=doctor["id"], closed_on=date(2026, 11, 18)),
            ServiceClosedDate(service_id=other["id"], closed_on=date(2026, 11, 23)),
            ClosedDate(closed_on=date(2026, 11, 24)),
            Appointment(
                name="Existing visit on closed day", phone="+380980000001",
                service=doctor["name"], service_id=doctor["id"],
                preferred_date=date(2026, 11, 18), preferred_time=time(10),
            ),
        ])
        session.commit()

    for path in ("/admin", "/admin?day=2026-11-18"):
        response = client.get(path)
        assert response.status_code == 200
        assert '<time datetime="2026-11-23">23.11.2026</time>' in response.text
        assert 'day=2026-11-17#appointment-diary" aria-label="Попередній день"' in response.text
        assert 'day=2026-11-25#appointment-diary" aria-label="Наступний день"' in response.text
        assert "Existing visit on closed day" not in response.text

    previous = client.get("/admin?day=2026-11-17").text
    assert 'day=2026-11-23#appointment-diary">Найближчий робочий день' in previous
    assert "Existing visit on closed day" in client.get("/admin/appointments?day=2026-11-18").text
    assert client.post("/admin/appointments/manual", data={
        "preferred_date": "2026-11-24", "preferred_time": "10:00", "phone": "0671234567",
    }).status_code == 409

    with Session(app_db) as session:
        service = session.get(BookingService, doctor["id"])
        service.weekdays_mask = 0
        session.commit()
    html = client.get("/admin").text
    assert "Для огляду лікаря немає робочих днів." in html
    assert "Гортання щоденника" not in html and "data-notebook-start=" not in html
    assert 'href="/admin/appointments">Усі записи' in html


def test_notebook_and_online_form_share_future_service_slots(
    client: TestClient, app_db: Engine, monkeypatch: MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 17, 18, 55, tzinfo=main.KYIV_TZ)
    monkeypatch.setattr(main, "kyiv_now", lambda: now)
    service = client.get("/api/booking-services").json()[0]
    with Session(app_db) as session:
        session.add(ServiceSchedule(service_id=service["id"], intervals=[(14 * 60, 21 * 60)]))
        session.add(Appointment(
            name="Earlier patient", phone="+380980000001", service=service["name"],
            service_id=service["id"], preferred_date=now.date(), preferred_time=time(15),
        ))
        session.commit()

    params = {"preferred_date": now.date().isoformat(), "service": service["name"]}
    manual = {
        "preferred_date": params["preferred_date"], "phone": "0671234567",
    }

    def check_starts(expected: list[str]) -> str:
        online = client.get("/api/availability", params=params)
        assert online.status_code == 200
        assert [slot["start_time"] for slot in online.json()] == [f"{start}:00" for start in expected]
        notebook = client.get("/admin", params={"day": params["preferred_date"]})
        assert notebook.status_code == 200
        assert re.findall(r'data-notebook-start="([^"]+)"', notebook.text) == expected
        return notebook.text

    notebook = check_starts(["19:00", "19:30", "20:00", "20:30"])
    assert "Час минув" in notebook and "Earlier patient" in notebook
    now = now.replace(hour=19, minute=0)
    check_starts(["19:30", "20:00", "20:30"])
    for response in (
        client.post("/admin/appointments/manual", data={**manual, "preferred_time": "19:00"}),
        client.post("/api/appointments", json={
            **manual, "preferred_time": "19:00", "name": "Client", "service": service["name"],
        }),
    ):
        assert response.status_code == 409
        assert "Цей час уже минув" in response.json()["detail"]

    result = client.post("/admin/appointments/manual", data={
        **manual, "preferred_time": "19:40",
    }, follow_redirects=False)
    assert result.status_code == 303
    assert "19:40" in check_starts(["20:30"])
    now = now.replace(hour=21)
    check_starts([])

    previous_day = (now.date() - timedelta(days=1)).isoformat()
    old_notebook = client.get("/admin", params={"day": previous_day})
    assert old_notebook.status_code == 200
    assert "Час минув" in old_notebook.text and "data-notebook-start=" not in old_notebook.text
    assert client.post("/admin/appointments/manual", data={
        **manual, "preferred_date": previous_day, "preferred_time": "14:00",
    }).status_code == 409


def test_admin_can_delete_appointments_and_reopen_their_time(
    client: TestClient, anonymous_client: TestClient, raw_client: TestClient,
    app_db: Engine, monkeypatch: MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 17, 9, 5, tzinfo=main.KYIV_TZ)
    monkeypatch.setattr(main, "kyiv_now", lambda: now)
    service = client.get("/api/booking-services").json()[0]
    day = now.date().isoformat()
    params = {"service": service["name"], "preferred_date": day}

    def starts() -> list[str]:
        return [slot["start_time"] for slot in anonymous_client.get(
            "/api/availability", params=params,
        ).json()]

    first, second, *_ = starts()

    def book(name: str, start: str) -> int:
        response = anonymous_client.post("/api/appointments", json={
            **params, "name": name, "phone": "+380500000001", "preferred_time": start,
        })
        assert response.status_code == 201
        return response.json()["id"]

    removal_id = book("Remove this visit", first)
    book("Keep this visit", second)
    with Session(app_db) as session:
        past = Appointment(
            name="Past visit", phone="+380500000001", service=service["name"],
            preferred_date=now.date(), preferred_time=time(9),
            duration_minutes=service["duration_minutes"],
        )
        session.add(past)
        session.commit()
        past_id = past.id
        past_version = past.updated_at.isoformat()
        version = session.get(Appointment, removal_id).updated_at.isoformat()

    assert first not in starts() and second not in starts()
    records = f"/admin/appointments?day={day}"
    assert 'aria-label="Видалити: Remove this visit"' in client.get(records).text
    endpoint = f"/admin/appointments/{removal_id}/delete"
    data = {"version": version, "return_to": records}
    assert anonymous_client.post(endpoint, data=data).status_code == 401
    assert raw_client.post(endpoint, data=data, auth=client.auth).status_code == 403
    confirmation = client.post(endpoint, data=data)
    assert confirmation.status_code == 409
    assert confirmation.json()["detail"] == {"confirmation_required": "upcoming_appointment_removal"}
    assert client.post(endpoint, data={
        **data, "confirmed": "true", "version": "2000-01-01",
    }).status_code == 409
    assert first not in starts() and second not in starts()

    assert client.post(endpoint, data={**data, "confirmed": "true"}, headers={
        "Accept": "application/json",
    }).status_code == 204
    result = client.post(f"/admin/appointments/{past_id}/delete", data={
        "version": past_version, "return_to": records,
    }, follow_redirects=False)
    assert result.status_code == 303 and result.headers["location"] == records
    html = client.get(records).text
    assert "Remove this visit" not in html and "Past visit" not in html
    assert "Keep this visit" in html
    assert first in starts() and second not in starts()


def test_service_price_can_be_unset_without_changing_explicit_zero(client: TestClient) -> None:
    service, other_service, *_ = client.get("/api/booking-services").json()
    changes = [
        {"id": service["id"], "version": service["updated_at"], "name": service["name"], "price_uah": ""},
        {"id": other_service["id"], "version": other_service["updated_at"], "name": other_service["name"]},
        {"name": "Service without a price"},
        {"name": "Free service", "price_uah": 0},
    ]
    assert client.post("/admin/changes", json={"services": changes}).status_code == 200
    prices = {row["name"]: row["price_uah"] for row in client.get("/api/booking-services").json()}
    assert prices[service["name"]] is None
    assert prices[other_service["name"]] == other_service["price_uah"]
    assert prices["Service without a price"] is None
    assert prices["Free service"] == 0

    booking = client.get("/booking").text
    blank_card = booking.split('value="Service without a price"', 1)[1].split("</label>", 1)[0]
    free_card = booking.split('value="Free service"', 1)[1].split("</label>", 1)[0]
    assert "грн" not in blank_card and "30 хв" in blank_card
    assert "0 грн" in free_card


def test_service_weekdays_restrict_availability_and_booking(client: TestClient) -> None:
    service, other_service, *_ = client.get("/api/booking-services").json()
    open_day = current_date() + timedelta(days=2)
    excluded_day = open_day + timedelta(days=1)
    mask = 1 << open_day.weekday()
    version = datetime.fromisoformat(service["updated_at"]).replace(tzinfo=UTC).isoformat()
    edit = {
        "id": service["id"], "version": version, "name": service["name"],
        "weekdays_mask": mask,
    }
    assert client.post("/admin/changes", json={"services": [edit]}).status_code == 200
    services = client.get("/api/booking-services").json()
    assert next(row for row in services if row["id"] == service["id"])["weekdays_mask"] == mask

    def availability(day, name: str) -> list:
        response = client.get("/api/availability", params={"preferred_date": day, "service": name})
        assert response.status_code == 200
        return response.json()

    assert availability(excluded_day, service["name"]) == []
    assert availability(excluded_day, other_service["name"])
    payload = {
        "name": "Client", "phone": "+380501234567", "service": service["name"],
        "preferred_time": availability(open_day, service["name"])[0]["start_time"],
        "preferred_date": excluded_day.isoformat(),
    }
    response = client.post("/api/appointments", json=payload)
    assert response.status_code == 400
    assert "день тижня" in response.json()["detail"]
    assert client.post(
        "/api/appointments", json={**payload, "preferred_date": open_day.isoformat()},
    ).status_code == 201


def test_admin_catalog_changes_update_public_pages(
    client: TestClient, anonymous_client: TestClient,
) -> None:
    category = {"id": "frames", "version": "", "name": "Оптичні оправи"}
    product = {
        "category": "frames", "name": "Тестова оправа", "description": "Металева",
        "price": "1890.50",
    }
    batch = {"categories": [category], "products": [product]}
    assert anonymous_client.post("/admin/changes", json=batch).status_code == 401
    assert client.post("/admin/changes", json=batch).status_code == 200
    assert "Оптичні оправи" in client.get("/").text
    page = client.get("/catalog/frames").text
    assert "Оптичні оправи" in page and "Тестова оправа" in page and "1 890,50 грн" in page

    stored, = client.get("/api/products").json()
    edit = {
        **product, "id": stored["id"], "version": stored["updated_at"],
        "category": "sunglasses", "name": "Тестові окуляри",
    }
    assert client.post("/admin/changes", json={
        "categories": [category], "products": [edit],
    }).status_code == 409
    assert "Тестова оправа" in client.get("/catalog/frames").text

    assert client.post("/admin/changes", json={"products": [edit]}).status_code == 200
    assert "Тестова оправа" not in client.get("/catalog/frames").text
    assert "Тестові окуляри" in client.get("/catalog/sunglasses").text
    updated, = client.get("/api/products").json()
    assert client.post("/admin/changes", json={"remove_products": [{
        "id": updated["id"], "version": updated["updated_at"],
    }]}).status_code == 200
    assert client.get("/api/products").json() == []


def test_category_removal_hides_products_until_they_are_moved(client: TestClient) -> None:
    category = {"id": "frames", "version": ""}
    response = client.post("/api/products", json={"category": "FRAMES", "name": "Металева оправа"})
    assert response.status_code == 201
    product = response.json()
    assert client.post("/admin/changes", json={
        "remove_categories": [category],
        "products": [{"category": "frames", "name": "Нова оправа", "price": 0}],
    }).status_code == 409
    assert client.get("/catalog/frames").status_code == 200

    assert client.post("/admin/changes", json={"remove_categories": [category]}).status_code == 200
    assert client.post("/admin/changes", json={"remove_categories": [category]}).status_code == 409
    home = client.get("/").text
    assert 'href="/catalog/frames"' not in home
    assert 'href="/catalog/sunglasses"' in home
    assert client.get("/catalog/frames").status_code == 404
    assert "Металева оправа" not in client.get("/catalog").text
    assert client.get("/api/products").json() == []
    admin = client.get("/admin").text
    assert "Металева оправа" in admin and "Категорію видалено" in admin
    assert client.post("/api/products", json={
        "category": "frames", "name": "Нова оправа",
    }).status_code == 409

    edit = {
        "id": product["id"], "version": product["updated_at"], "category": "sunglasses",
        "name": product["name"], "price": 0,
    }
    assert client.post("/admin/changes", json={"products": [edit]}).status_code == 200
    assert "Металева оправа" in client.get("/catalog/sunglasses").text


def test_last_category_removal_cleans_up_home_navigation(client: TestClient) -> None:
    removals = [
        {"id": slug, "version": ""}
        for slug in ["frames", "sunglasses", "contact-lenses", "kids-frames"]
    ]
    assert client.post("/admin/changes", json={"remove_categories": removals}).status_code == 200
    home = client.get("/").text
    assert 'id="catalog"' not in home and 'href="/#catalog"' not in home
    assert client.get("/booking").status_code == 200


def test_new_category_and_its_products_share_one_save(
    client: TestClient, anonymous_client: TestClient, app_db: Engine,
) -> None:
    slug = "category-0123456789abcdef0123456789abcdef"
    url = "/catalog/" + slug
    category = {"id": slug, "name": "Спортивні оправи"}
    product = {"category": slug, "name": "Оправа для бігу", "price": 800}
    changes = {"create_categories": [category], "products": [product]}

    assert anonymous_client.post("/admin/changes", json=changes).status_code == 401
    assert client.post("/admin/changes", json={
        "create_categories": [{**category, "name": "  ОПРАВИ  "}],
    }).status_code == 409
    assert client.post("/admin/changes", json={
        **changes, "products": [{**product, "category": "unknown"}],
    }).status_code == 409
    assert client.get(url).status_code == 404
    assert client.post("/admin/changes", json=changes).status_code == 200
    assert url in anonymous_client.get("/").text
    assert "Оправа для бігу" in anonymous_client.get(url).text
    assert client.get("/catalog/frames").status_code == 200

    def version() -> dict:
        with Session(app_db) as session:
            return {"id": slug, "version": session.get(CatalogCategory, slug).updated_at.isoformat()}

    assert client.post("/admin/changes", json={"categories": [{
        **version(), "name": "Оправи для спорту",
    }]}).status_code == 200
    assert "Оправи для спорту" in anonymous_client.get(url).text
    assert client.post("/admin/changes", json={"remove_categories": [version()]}).status_code == 200
    assert anonymous_client.get(url).status_code == 404
    assert anonymous_client.get("/api/products").json() == []
    assert client.post("/admin/changes", json={"restore_categories": [version()]}).status_code == 200
    assert "Оправа для бігу" in anonymous_client.get(url).text


def test_catalog_order_and_permanent_removal_preserve_products(
    client: TestClient, app_db: Engine,
) -> None:
    assert client.post("/admin/changes", json={"products": [
        {"name": "First", "category": "frames", "price": 100},
        {"name": "Second", "category": "sunglasses", "price": 200},
    ]}).status_code == 200
    first, second = client.get("/api/products").json()
    categories = ["frames", "sunglasses", "contact-lenses", "kids-frames"]
    changes = {
        "category_order": {"previous": categories, "positions": categories[::-1]},
        "product_order": {
            "previous": [first["id"], second["id"]], "positions": [second["id"], first["id"]],
        },
    }
    assert client.post("/admin/changes", json=changes).status_code == 200
    assert [row["name"] for row in client.get("/api/products").json()] == ["Second", "First"]
    assert re.findall(r'class="landing-category" href="/catalog/([^"]+)"', client.get("/").text) == (
        categories[::-1]
    )
    assert client.post("/admin/changes", json=changes).status_code == 409

    def category_version() -> dict:
        with Session(app_db) as session:
            row = session.get(CatalogCategory, "frames")
            return {"id": row.slug, "version": row.updated_at.isoformat()}

    assert client.post("/admin/changes", json={"remove_categories": [
        category_version(),
    ]}).status_code == 200
    purge = {"purge_categories": [category_version()]}
    assert client.post("/admin/changes", json=purge).status_code == 409
    assert 'data-removed-category="frames"' in client.get("/admin").text
    assert client.post("/admin/changes", json={**purge, "products": [{
        "id": first["id"], "version": first["updated_at"], "name": first["name"],
        "category": "sunglasses", "price": 100,
    }]}).status_code == 200
    assert client.get("/catalog/frames").status_code == 404
    assert 'data-removed-category="frames"' not in client.get("/admin").text
    assert client.post("/admin/changes", json={"restore_categories": [
        category_version(),
    ]}).status_code == 409
    assert [row["name"] for row in client.get("/api/products").json()] == ["Second", "First"]


def test_photo_visibility_follows_category_removal_and_restoration(
    client: TestClient, anonymous_client: TestClient, app_db: Engine,
) -> None:
    buffer = BytesIO()
    with Image.new("RGB", (32, 24), "white") as image:
        image.save(buffer, "JPEG")
    photo = b64encode(buffer.getvalue()).decode("ascii")
    product = {"category": "frames", "name": "Оправи з фото", "price": 500, "photo": photo}
    assert client.post("/admin/changes", json={"products": [
        {**product, "photo": b64encode(b"not a photo").decode("ascii")},
    ]}).status_code == 422
    assert client.get("/api/products").json() == []
    assert client.post("/admin/changes", json={"products": [product]}).status_code == 200
    saved, = client.get("/api/products").json()
    url = saved["image_url"]
    response = anonymous_client.get(url)
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-cache"
    cached = {"If-None-Match": response.headers["etag"]}
    unchanged = anonymous_client.get(url, headers=cached)
    assert unchanged.status_code == 304 and unchanged.content == b""
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (32, 24)
    assert url in anonymous_client.get("/catalog/frames").text
    assert anonymous_client.get("/admin" + url).status_code == 401
    assert client.post("/admin/changes", json={"remove_categories": [{
        "id": "frames", "version": "",
    }]}).status_code == 200
    assert anonymous_client.get(url, headers=cached).status_code == 404
    private_photo = client.get("/admin" + url)
    assert private_photo.status_code == 200 and private_photo.headers["cache-control"] == "no-store"
    with Session(app_db) as session:
        category = session.get(CatalogCategory, "frames")
        restore = {"id": "frames", "version": category.updated_at.isoformat()}
    assert client.post("/admin/changes", json={"restore_categories": [restore]}).status_code == 200
    assert client.post("/admin/changes", json={"restore_categories": [restore]}).status_code == 409
    assert anonymous_client.get(url).status_code == 200
    assert client.post("/admin/changes", json={"products": [{
        **product, "id": saved["id"], "version": saved["updated_at"], "photo": "remove",
    }]}).status_code == 200
    assert anonymous_client.get(url).status_code == 404
    assert url not in anonymous_client.get("/catalog/frames").text


def test_work_schedule_sets_hours_and_booking_uses_remaining_time(client: TestClient) -> None:
    def schedule() -> dict:
        page = client.get("/admin").text
        return json.loads(re.search(r"data-work-schedule='([^']+)'", page)[1])

    initial = schedule()
    assert (initial["start_time"], initial["end_time"]) == ("09:00", "17:00")
    assert initial["breaks"] == [{"start_time": "13:00", "end_time": "14:00"}]
    workday = {"start_time": "08:30", "end_time": "18:00"}
    lunch = {"start_time": "12:00", "end_time": "13:00"}
    changes = {"version": initial["version"], "workday": workday, "breaks": [lunch]}
    assert client.post("/admin/changes", json={"work_schedule": changes}).status_code == 200
    assert client.post("/admin/changes", json={"work_schedule": changes}).status_code == 409
    updated = schedule()
    assert updated["breaks"] == [lunch]
    assert client.post("/admin/changes", json={"work_schedule": {
        **changes, "version": updated["version"],
        "breaks": [lunch, {"start_time": "12:30", "end_time": "13:30"}],
    }}).status_code == 422
    assert schedule() == updated

    day = (current_date() + timedelta(days=2)).isoformat()
    service = "Підбір окулярів"
    params = {"preferred_date": day, "service": service}
    assert client.post("/api/appointments", json={
        **params, "preferred_time": "08:30", "name": "Client", "phone": "+380501234567",
    }).status_code == 201
    starts = [row["start_time"] for row in client.get("/api/availability", params=params).json()]
    assert starts == [
        "09:15:00", "10:00:00", "10:45:00",
        "13:00:00", "13:45:00", "14:30:00", "15:15:00", "16:00:00", "16:45:00",
    ]

    change = {
        "work_schedule": {"version": updated["version"]},
    }
    confirm_schedule_change(client, change)
    assert client.get("/api/availability", params=params).json() == []
    assert schedule()["start_time"] == ""


def test_per_service_hours_and_closures_respect_shared_bookings(
    client: TestClient, app_db: Engine,
) -> None:
    first, second, *_ = client.get("/api/booking-services").json()
    day = current_date() + timedelta(days=2)
    closed = day + timedelta(days=1)
    common_closed = day + timedelta(days=2)
    inherited = service_schedule_version(None)
    schedule = {
        "id": first["id"],
        "schedule_version": inherited,
        "schedule": {
            "workday": {"start_time": "08:00", "end_time": "18:00"},
            "breaks": [{"start_time": "12:00", "end_time": "14:00"}],
        },
        "closed_days": {"close": [closed.isoformat()], "reason": "Відпустка"},
    }
    assert client.post("/admin/changes", json={
        "service_availability": [schedule],
        "closed_days": {"close": [common_closed.isoformat()]},
    }).status_code == 200
    assert client.post("/admin/changes", json={"service_availability": [schedule]}).status_code == 409

    def times(service: dict, on: date = day) -> list[str]:
        response = client.get(
            "/api/availability", params={"preferred_date": on, "service": service["name"]},
        )
        assert response.status_code == 200
        return [slot["start_time"] for slot in response.json()]

    def book(service: dict, on: date, at: str) -> int:
        return client.post("/api/appointments", json={
            "service": service["name"], "preferred_date": on.isoformat(), "preferred_time": at,
            "name": "Client", "phone": "+380501234567",
        }).status_code

    assert times(first)[0] == "08:00:00"
    assert times(second)[0] == "09:00:00"
    assert "12:00:00" not in times(first)
    assert times(first, closed) == []
    assert times(second, closed)
    assert times(first, common_closed) == times(second, common_closed) == []
    assert book(first, closed, "09:00") == 400
    assert book(first, day, "12:00") == 400
    assert book(first, day, "09:00") == 201
    assert book(second, day, "09:00") == 409
    assert "09:00:00" not in times(second)

    with Session(app_db) as session:
        version = service_schedule_version(session.get(ServiceSchedule, first["id"]))
        closure = session.exec(select(ServiceClosedDate)).one()
        reopen = {str(closure.id): closure.updated_at.isoformat()}
    assert client.post("/admin/changes", json={"service_availability": [{
        "id": first["id"], "schedule_version": version, "closed_days": {"reopen": reopen},
    }]}).status_code == 200
    assert times(first, closed)[0] == "09:00:00"
    assert times(first, common_closed) == []


def test_booking_calendar_reopens_a_full_date_when_an_appointment_is_canceled(
    client: TestClient, app_db: Engine, monkeypatch: MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 27, 9, 30, tzinfo=main.KYIV_TZ)
    monkeypatch.setattr(main, "kyiv_now", lambda: now)
    today = now.date()
    full_day, blocked_day, closed_day, service_closed_day = (
        today + timedelta(days=offset) for offset in range(1, 5)
    )
    doctor, other = client.get("/api/booking-services").json()[:2]
    with Session(app_db) as session:
        visit = Appointment(
            name="Test appointment", phone="", service=other["name"], service_id=other["id"],
            preferred_date=full_day, preferred_time=time(9), duration_minutes=60,
        )
        session.add_all([
            visit,
            BookingBlock(service_id=doctor["id"], day=blocked_day, start_minute=540, end_minute=600),
            ClosedDate(closed_on=closed_day),
            ServiceClosedDate(service_id=doctor["id"], closed_on=service_closed_day),
            ServiceSchedule(service_id=doctor["id"], intervals=[(540, 600)]),
        ])
        session.commit()
        visit_id, version = visit.id, visit.updated_at.isoformat()

    params = {"service": doctor["name"], "preferred_date": full_day.isoformat()}
    endpoint = "/api/availability/calendar"
    response = client.get(endpoint, params=params)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    result = response.json()
    assert result["slots"] == []
    assert {day.isoformat() for day in (today, full_day, blocked_day, closed_day, service_closed_day)}.isdisjoint(
        result["dates"],
    )
    assert (today + timedelta(days=5)).isoformat() in result["dates"]
    other_dates = client.get(endpoint, params={"service": other["name"]}).json()["dates"]
    assert {full_day.isoformat(), blocked_day.isoformat(), service_closed_day.isoformat()} <= set(other_dates)
    assert closed_day.isoformat() not in other_dates

    assert client.post(f"/admin/appointments/{visit_id}/status", data={
        "status": "canceled", "version": version,
    }, follow_redirects=False).status_code == 303
    reopened = client.get(endpoint, params=params).json()
    assert full_day.isoformat() in reopened["dates"]
    assert reopened["slots"] == client.get("/api/availability", params=params).json()
    assert [slot["start_time"] for slot in reopened["slots"]] == ["09:00:00", "09:30:00"]


def test_weekday_hours_apply_per_service_in_booking_and_notebook(
    client: TestClient, app_db: Engine, monkeypatch: MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 17, 11, tzinfo=main.KYIV_TZ)
    monkeypatch.setattr(main, "kyiv_now", lambda: now)
    doctor, other = client.get("/api/booking-services").json()[:2]
    with Session(app_db) as session:
        session.add(ServiceSchedule(service_id=doctor["id"], intervals=[(600, 720)]))
        session.commit()
    missing = service_schedule_version(None)
    hours = {
        "2": {"version": missing, "schedule": {
            "workday": {"start_time": "14:00", "end_time": "16:30"},
            "breaks": [{"start_time": "15:00", "end_time": "16:00"}],
        }},
        "3": {"version": missing, "schedule": {
            "workday": {"start_time": "11:00", "end_time": "12:00"},
        }},
        "4": {"version": missing, "schedule": {}},
    }

    def draft(schedules: dict) -> dict:
        return {"service_availability": [{
            "id": doctor["id"], "weekday_schedules": schedules,
        }]}

    def save(schedules: dict) -> int:
        return client.post("/admin/changes", json=draft(schedules)).status_code

    def starts(service: dict, day: str) -> list[str]:
        response = client.get("/api/availability", params={
            "preferred_date": day, "service": service["name"],
        })
        assert response.status_code == 200
        return [slot["start_time"] for slot in response.json()]

    wednesday, thursday, friday = "2026-11-18", "2026-11-19", "2026-11-20"
    assert save(hours) == 200
    assert save({"7": hours["2"]}) == 422
    assert starts(doctor, wednesday) == ["14:00:00", "14:30:00", "16:00:00"]
    assert starts(doctor, thursday) == ["11:00:00", "11:30:00"]
    assert starts(doctor, friday) == []
    assert starts(doctor, "2026-11-23")[0] == "10:00:00"
    assert starts(other, wednesday)[0] == "09:00:00"

    calendar = client.get("/api/availability/calendar", params={
        "service": doctor["name"], "preferred_date": wednesday,
    }).json()
    assert wednesday in calendar["dates"] and thursday in calendar["dates"] and friday not in calendar["dates"]
    assert [slot["start_time"] for slot in calendar["slots"]] == starts(doctor, wednesday)
    diary = client.get("/admin", params={"day": wednesday}).text
    assert re.findall(r'data-notebook-start="([^"]+)"', diary) == ["14:00", "14:30", "16:00"]
    assert 'day=2026-11-21#appointment-diary" aria-label="Наступний день"' in client.get(
        "/admin", params={"day": thursday},
    ).text
    booking = {
        "preferred_date": wednesday, "preferred_time": "15:00",
        "name": "Test client", "phone": "+380501234567", "service": doctor["name"],
    }
    assert client.post("/api/appointments", json=booking).status_code == 400
    assert client.post("/admin/appointments/manual", data=booking).status_code == 409
    assert client.post("/api/appointments", json={**booking, "preferred_time": "14:00"}).status_code == 201
    assert starts(doctor, wednesday) == ["14:30:00", "16:00:00"]

    assert save({"2": {"version": missing, "schedule": None}}) == 409
    with Session(app_db) as session:
        version = service_schedule_version(session.get(ServiceWeekdaySchedule, (doctor["id"], 2)))
    confirm_schedule_change(client, draft({"2": {"version": version, "schedule": None}}))
    assert starts(doctor, wednesday)[0] == "10:00:00"
    assert starts(doctor, thursday) == ["11:00:00", "11:30:00"]


def test_open_booking_forms_reject_a_time_removed_by_a_schedule_change(client: TestClient) -> None:
    doctor = client.get("/api/booking-services").json()[0]
    day = current_date() + timedelta(days=2)
    params = {"preferred_date": day.isoformat(), "service": doctor["name"]}
    assert "09:30:00" in {
        slot["start_time"] for slot in client.get("/api/availability", params=params).json()
    }
    assert client.post("/admin/changes", json={
        "service_availability": [{
            "id": doctor["id"], "schedule_version": service_schedule_version(None),
            "schedule": {"workday": {"start_time": "09:15", "end_time": "12:15"}},
        }],
    }).status_code == 200
    form = {
        **params, "name": "Test client", "phone": "+380501234567", "preferred_time": "09:30",
        "expected_duration_minutes": doctor["duration_minutes"],
    }
    assert client.post("/api/appointments", json=form).status_code == 409
    manual = {**form, "expected_duration_minutes": str(doctor["duration_minutes"])}
    assert client.post("/admin/appointments/manual", data=manual).status_code == 409
    assert client.post("/api/appointments", json={**form, "preferred_time": "09:45"}).status_code == 201


def test_schedule_review_rolls_back_until_the_current_affected_visits_are_confirmed(
    client: TestClient, anonymous_client: TestClient, app_db: Engine,
) -> None:
    doctor = client.get("/api/booking-services").json()[0]
    day = current_date() + timedelta(days=2)
    appointment = {
        "preferred_date": day.isoformat(), "service": doctor["name"],
        "name": "Private test patient", "phone": "+380501234567",
    }
    assert client.post("/api/appointments", json={
        **appointment, "preferred_time": "10:00",
    }).status_code == 201
    draft = {"service_availability": [{
        "id": doctor["id"], "schedule_version": service_schedule_version(None),
        "schedule": {"workday": {"start_time": "11:00", "end_time": "17:00"}},
    }]}
    first = client.post("/admin/changes", json=draft)
    assert first.status_code == 409
    warning = first.json()["detail"]
    assert warning["kind"] == "schedule_review" and warning["total"] == 1
    assert warning["appointments"] == [{
        "date": day.strftime("%d.%m.%Y"), "time": "10:00", "service": doctor["name"],
    }]
    assert "Private test patient" not in first.text and appointment["phone"] not in first.text
    with Session(app_db) as session:
        assert session.get(ServiceSchedule, doctor["id"]) is None
        assert session.exec(select(AdminAuditEvent)).first() is None
    assert anonymous_client.get("/admin/audit").status_code == 401

    assert client.post("/api/appointments", json={
        **appointment, "preferred_time": "10:30",
    }).status_code == 201
    stale = client.post("/admin/changes", json={
        **draft, "schedule_acknowledgement": warning["token"],
    })
    assert stale.status_code == 409
    updated = stale.json()["detail"]
    assert updated["total"] == 2 and updated["token"] != warning["token"]
    assert client.post("/admin/changes", json={
        **draft, "schedule_acknowledgement": updated["token"],
    }).status_code == 200
    with Session(app_db) as session:
        assert len(session.exec(select(Appointment)).all()) == 2
        event = session.exec(select(AdminAuditEvent)).one()
        assert event.actor == "admin" and event.action == "schedule_changed"
    seen = client.get("/admin/audit").text
    assert "Змінено графік" in seen and "типові години" in seen
    assert "Private test patient" not in seen and appointment["phone"] not in seen


def test_batch_applies_booking_service_and_period_together(client: TestClient, app_db: Engine) -> None:
    day = current_date() + timedelta(days=5)
    with Session(app_db) as session:
        current = Appointment(
            name="First", phone="+380980000001", service="Consultation",
            preferred_date=day, preferred_time=time(9), status=AppointmentStatus.confirmed,
        )
        alternate = Appointment(
            name="Alternate", phone="+380980000002", service="Consultation",
            preferred_date=day, preferred_time=time(9), status=AppointmentStatus.canceled,
        )
        session.add_all([current, alternate])
        session.commit()
        edits = [
            {"id": alternate.id, "version": alternate.updated_at.isoformat(), "status": "confirmed"},
            {"id": current.id, "version": current.updated_at.isoformat(), "status": "canceled"},
        ]
        current_id, alternate_id = current.id, alternate.id
    services = client.get("/api/booking-services").json()
    service = services[0]
    last = day + timedelta(days=2)
    response = client.post("/admin/changes", json={
        "appointments": edits,
        "services": [{
            "id": service["id"], "version": service["updated_at"], "name": service["name"],
            "duration_minutes": service["duration_minutes"], "price_uah": 725,
        }],
        "service_order": [row["id"] for row in reversed(services)],
        "closed_periods": [{"start": day.isoformat(), "end": last.isoformat(), "reason": "Holiday"}],
    })
    assert response.status_code == 200, response.text
    updated = client.get("/api/booking-services").json()
    assert updated[-1]["id"] == service["id"]
    assert updated[-1]["price_uah"] == 725
    for offset in range(3):
        date = (day + timedelta(days=offset)).isoformat()
        assert client.get("/api/availability", params={"preferred_date": date}).json() == []
    with Session(app_db) as session:
        assert session.get(Appointment, current_id).status == AppointmentStatus.canceled
        assert session.get(Appointment, alternate_id).status == AppointmentStatus.confirmed


def test_batch_conflict_rolls_back_other_sections(client: TestClient, app_db: Engine) -> None:
    with Session(app_db) as session:
        appointment = Appointment(name="Pending", phone="+380980000003", service="Consultation")
        session.add(appointment)
        session.commit()
        edit = {
            "id": appointment.id, "version": appointment.updated_at.isoformat(), "status": "canceled",
        }
    services = client.get("/api/booking-services").json()
    day = (current_date() + timedelta(days=4)).isoformat()
    response = client.post("/admin/changes", json={
        "appointments": [edit],
        "services": [{"name": services[0]["name"]}],
        "closed_periods": [{"start": day}],
    })
    assert response.status_code == 409
    assert client.get("/api/closed-dates").json() == []
    assert client.get("/api/booking-services").json() == services
    with Session(app_db) as session:
        assert session.get(Appointment, edit["id"]).status == AppointmentStatus.new


def test_closed_period_overlap_and_reopening(
    client: TestClient, anonymous_client: TestClient, app_db: Engine,
) -> None:
    day = current_date() + timedelta(days=8)
    days = [(day + timedelta(days=offset)).isoformat() for offset in range(4)]
    assert client.post("/admin/changes", json={
        "closed_periods": [{"start": days[1], "reason": "Existing note"}],
    }).status_code == 200
    assert client.post("/admin/changes", json={
        "closed_periods": [{"start": days[0], "end": days[2], "reason": "New note"}],
    }).status_code == 200
    closed = anonymous_client.get("/api/closed-dates").json()
    assert [row["closed_on"] for row in closed] == days[:3]
    assert all("reason" not in row for row in closed)
    with Session(app_db) as session:
        reasons = session.exec(select(ClosedDate.reason).order_by(ClosedDate.closed_on)).all()
        assert reasons == ["New note", "Existing note", "New note"]

    first = closed[0]
    previous = {first["id"]: first["updated_at"]}
    assert client.post("/admin/changes", json={
        "closed_periods": [{"previous": previous, "start": days[0], "reason": "Updated"}],
    }).status_code == 200
    assert client.post("/admin/changes", json={
        "closed_periods": [{"previous": previous}],
    }).status_code == 409
    closed = client.get("/api/closed-dates").json()
    with Session(app_db) as session:
        assert session.get(ClosedDate, first["id"]).reason == "Updated"
    assert client.post("/admin/changes", json={
        "closed_periods": [{"previous": {closed[0]["id"]: closed[0]["updated_at"]}}],
    }).status_code == 200
    assert [row["closed_on"] for row in client.get("/api/closed-dates").json()] == days[1:3]
    assert client.post("/admin/changes", json={
        "closed_periods": [{"start": days[3], "end": days[0]}],
    }).status_code == 422


def test_delete_catalog_rows_keeps_empty_lists_and_allows_readding(
    client: TestClient, app_db: Engine,
) -> None:
    services = client.get("/api/booking-services").json()
    slots = client.get("/api/time-slots").json()
    service, slot = services[0], slots[0]
    with Session(app_db) as session:
        appointment = Appointment(
            name="Existing booking", phone="+380980000001", service=service["name"],
            preferred_date=current_date() + timedelta(days=5), preferred_time=time(9),
        )
        session.add(appointment)
        session.commit()
        appointment_id = appointment.id

    def versions(rows: list[dict]) -> list[dict]:
        return [{"id": row["id"], "version": row["updated_at"]} for row in rows]

    change = {
        "remove_services": versions(services), "remove_time_slots": versions(slots),
    }
    confirm_schedule_change(client, change)
    assert client.get("/api/booking-services").json() == []
    assert client.get("/api/time-slots").json() == []
    dashboard = client.get("/admin").text
    assert service["name"] in client.get("/admin/appointments").text
    assert 'data-admin-edit="services"' not in dashboard
    assert 'data-admin-edit="time_slots"' not in dashboard
    with Session(app_db) as session:
        existing = session.get(Appointment, appointment_id)
        assert existing.service == service["name"]
        assert existing.preferred_time == time(9)

    response = client.post("/admin/changes", json={
        "services": [{"name": service["name"], "price_uah": 725}],
        "time_slots": [{"start_time": slot["start_time"], "end_time": "10:30"}],
    })
    assert response.status_code == 200, response.text
    restored = client.get("/api/booking-services").json()
    assert [(row["name"], row["price_uah"]) for row in restored] == [(service["name"], 725)]
    assert [row["end_time"] for row in client.get("/api/time-slots").json()] == ["10:30:00"]
    assert client.post("/admin/changes", json={
        "services": [{
            "id": restored[0]["id"], "version": restored[0]["updated_at"],
            "name": services[1]["name"], "price_uah": 725,
        }],
    }).status_code == 200
    assert [row["name"] for row in client.get("/api/booking-services").json()] == [services[1]["name"]]


def test_calendar_saves_nonconsecutive_dates_and_reopens_one_day(
    client: TestClient, app_db: Engine,
) -> None:
    start = current_date() + timedelta(days=5)
    days = [(start + timedelta(days=offset)).isoformat() for offset in range(6)]
    assert client.post("/admin/changes", json={
        "closed_days": {"close": [days[1], days[3]], "reason": "Existing note"},
    }).status_code == 200
    to_open, _ = client.get("/api/closed-dates").json()
    response = client.post("/admin/changes", json={
        "closed_days": {
            "close": [days[0], days[3], days[5]],
            "reopen": {to_open["id"]: to_open["updated_at"]},
        },
    })
    assert response.status_code == 200, response.text
    closed = client.get("/api/closed-dates").json()
    assert [row["closed_on"] for row in closed] == [days[0], days[3], days[5]]
    with Session(app_db) as session:
        reasons = session.exec(select(ClosedDate.reason).order_by(ClosedDate.closed_on)).all()
        assert reasons == ["", "Existing note", ""]
    assert client.get("/api/availability", params={"preferred_date": days[1]}).json()
    assert client.get("/api/availability", params={"preferred_date": days[3]}).json() == []
