from datetime import time, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.main import current_date
from app.models import Appointment, AppointmentStatus


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
