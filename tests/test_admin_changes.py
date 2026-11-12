import json
import re
from datetime import time, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.main import current_date
from app.models import Appointment, AppointmentStatus


def test_service_weekdays_restrict_availability_and_booking(client: TestClient) -> None:
    service, other_service, *_ = client.get("/api/booking-services").json()
    open_day = current_date() + timedelta(days=2)
    excluded_day = open_day + timedelta(days=1)
    mask = 1 << open_day.weekday()
    edit = {
        "id": service["id"], "version": service["updated_at"], "name": service["name"],
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
    assert starts[0] == "09:15:00"
    assert "11:15:00" in starts and "11:30:00" not in starts
    assert "13:00:00" in starts and starts[-1] == "17:15:00"

    assert client.post("/admin/changes", json={
        "work_schedule": {"version": updated["version"]},
    }).status_code == 200
    assert client.get("/api/availability", params=params).json() == []
    assert schedule()["start_time"] == ""


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


def test_closed_period_overlap_and_reopening(client: TestClient) -> None:
    day = current_date() + timedelta(days=8)
    days = [(day + timedelta(days=offset)).isoformat() for offset in range(4)]
    assert client.post("/admin/changes", json={
        "closed_periods": [{"start": days[1], "reason": "Existing note"}],
    }).status_code == 200
    assert client.post("/admin/changes", json={
        "closed_periods": [{"start": days[0], "end": days[2], "reason": "New note"}],
    }).status_code == 200
    closed = client.get("/api/closed-dates").json()
    assert [(row["closed_on"], row["reason"]) for row in closed] == [
        (days[0], "New note"), (days[1], "Existing note"), (days[2], "New note"),
    ]

    first = closed[0]
    previous = {first["id"]: first["updated_at"]}
    assert client.post("/admin/changes", json={
        "closed_periods": [{"previous": previous, "start": days[0], "reason": "Updated"}],
    }).status_code == 200
    assert client.post("/admin/changes", json={
        "closed_periods": [{"previous": previous}],
    }).status_code == 409
    closed = client.get("/api/closed-dates").json()
    assert closed[0]["reason"] == "Updated"
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

    response = client.post("/admin/changes", json={
        "remove_services": versions(services), "remove_time_slots": versions(slots),
    })
    assert response.status_code == 200, response.text
    assert client.get("/api/booking-services").json() == []
    assert client.get("/api/time-slots").json() == []
    dashboard = client.get("/admin").text
    assert service["name"] in dashboard  # The existing appointment still shows its original name.
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


def test_calendar_saves_nonconsecutive_dates_and_reopens_one_day(client: TestClient) -> None:
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
    assert [(row["closed_on"], row["reason"]) for row in closed] == [
        (days[0], ""), (days[3], "Existing note"), (days[5], ""),
    ]
    assert client.get("/api/availability", params={"preferred_date": days[1]}).json()
    assert client.get("/api/availability", params={"preferred_date": days[3]}).json() == []
