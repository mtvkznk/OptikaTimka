from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, Self

from fastapi import HTTPException
from pydantic import BaseModel, Field, PositiveInt, model_validator
from sqlmodel import Session, select

from app.models import (
    Appointment,
    AppointmentStatus,
    AvailableTimeSlot,
    AvailableTimeSlotCreate,
    BookingService,
    BookingServiceCreate,
    ClosedDate,
    utc_now,
)

MAX_PERIOD_DAYS = 366


class AppointmentChange(BaseModel):
    id: PositiveInt
    version: str = Field(max_length=48)
    status: AppointmentStatus


class ServiceChange(BookingServiceCreate):
    id: PositiveInt | None = None
    version: str | None = Field(default=None, max_length=48)


class SlotChange(AvailableTimeSlotCreate):
    id: PositiveInt | None = None
    version: str | None = Field(default=None, max_length=48)


class ClosedPeriodChange(BaseModel):
    previous: dict[PositiveInt, str] = Field(default_factory=dict, max_length=MAX_PERIOD_DAYS)
    start: date | None = None
    end: date | None = None
    reason: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.start is None:
            if not self.previous or self.end is not None:
                raise ValueError("A closure needs a start date")
        else:
            self.end = self.end or self.start
            if not 0 <= (self.end - self.start).days < MAX_PERIOD_DAYS:
                raise ValueError("The period must contain between 1 and 366 days")
        self.reason = self.reason.strip()
        return self


class AdminChanges(BaseModel):
    appointments: list[AppointmentChange] = Field(default_factory=list, max_length=1000)
    services: list[ServiceChange] = Field(default_factory=list, max_length=500)
    time_slots: list[SlotChange] = Field(default_factory=list, max_length=500)
    closed_periods: list[ClosedPeriodChange] = Field(default_factory=list, max_length=25)
    service_order: list[PositiveInt] | None = Field(default=None, max_length=1000)
    time_slot_order: list[PositiveInt] | None = Field(default=None, max_length=1000)


def closed_periods_for_display(closed_dates: Sequence[ClosedDate]) -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []
    for item in closed_dates:
        previous = periods[-1] if periods else None
        snapshot = {"id": item.id, "version": item.updated_at.isoformat()}
        if (
            previous
            and previous["reason"] == item.reason
            and (item.closed_on - previous["end"]).days == 1
            and len(previous["days"]) < MAX_PERIOD_DAYS
        ):
            previous["end"] = item.closed_on
            previous["days"][item.closed_on.isoformat()] = snapshot
        else:
            periods.append({
                "id": item.id,
                "start": item.closed_on,
                "end": item.closed_on,
                "reason": item.reason,
                "days": {item.closed_on.isoformat(): snapshot},
            })
    return periods


def verify_version(row: Any, version: str | None) -> None:
    if row is None or row.updated_at.isoformat() != version:
        raise HTTPException(409, "Дані змінилися. Скиньте чернетку, щоб завантажити актуальні дані.")


def apply_appointment_changes(session: Session, changes: list[AppointmentChange]) -> None:
    if not changes:
        return
    ids = {change.id for change in changes}
    if len(ids) != len(changes):
        raise HTTPException(422, "Один запис змінено кілька разів.")
    rows = {
        row.id: row
        for row in session.exec(
            select(Appointment).where(Appointment.id.in_(ids)).order_by(Appointment.id)
            .with_for_update()
        )
    }
    for change in changes:
        verify_version(rows.get(change.id), change.version)

    # Release canceled slots before reactivating another appointment for the same time.
    for change in changes:
        if change.status == AppointmentStatus.canceled:
            rows[change.id].status = change.status
            rows[change.id].updated_at = utc_now()
    session.flush()
    for change in changes:
        if change.status != AppointmentStatus.canceled:
            rows[change.id].status = change.status
            rows[change.id].updated_at = utc_now()


def apply_catalog_changes(
    session: Session,
    model: type[Any],
    changes: Sequence[ServiceChange | SlotChange],
    order: list[int] | None,
) -> None:
    if not changes and order is None:
        return
    rows = {
        row.id: row
        for row in session.exec(select(model).order_by(model.id).with_for_update())
    }
    edited: set[int] = set()
    for change in changes:
        if change.id is None:
            continue
        if change.id in edited:
            raise HTTPException(422, "Один рядок змінено кілька разів.")
        verify_version(rows.get(change.id), change.version)
        edited.add(change.id)
    if order is not None:
        if len(order) != len(rows) or set(order) != rows.keys():
            raise HTTPException(409, "Список змінився. Оновіть сторінку й перевірте порядок.")
        for index, item_id in enumerate(order, start=1):
            row = rows[item_id]
            if row.sort_order != index:
                row.sort_order = index
                row.updated_at = utc_now()

    next_order = max((row.sort_order for row in rows.values()), default=0)
    for change in changes:
        values = change.model_dump(exclude={"id", "version", "sort_order"})
        if change.id is None:
            next_order += 1
            session.add(model.model_validate({**values, "sort_order": next_order}))
        else:
            row = rows[change.id]
            row.sqlmodel_update(values)
            row.updated_at = utc_now()


def apply_closed_period_changes(session: Session, changes: list[ClosedPeriodChange]) -> None:
    if not changes:
        return
    rows = {
        row.id: row for row in session.exec(select(ClosedDate).order_by(ClosedDate.id).with_for_update())
    }
    removed: set[int] = set()
    for change in changes:
        for item_id, version in change.previous.items():
            if item_id in removed:
                raise HTTPException(422, "Періоди для редагування перетинаються.")
            verify_version(rows.get(item_id), version)
            removed.add(item_id)

    final = {row.closed_on: row.reason for item_id, row in rows.items() if item_id not in removed}
    for change in changes:
        if change.start is None or change.end is None:
            continue
        for offset in range((change.end - change.start).days + 1):
            day = change.start + timedelta(days=offset)
            final.setdefault(day, change.reason)

    for row in rows.values():
        if row.closed_on not in final:
            session.delete(row)
        else:
            reason = final.pop(row.closed_on)
            if row.reason != reason:
                row.reason = reason
                row.updated_at = utc_now()
    session.add_all(ClosedDate(closed_on=day, reason=reason) for day, reason in final.items())


def apply_admin_changes(session: Session, changes: AdminChanges) -> None:
    apply_appointment_changes(session, changes.appointments)
    apply_catalog_changes(session, BookingService, changes.services, changes.service_order)
    apply_catalog_changes(session, AvailableTimeSlot, changes.time_slots, changes.time_slot_order)
    apply_closed_period_changes(session, changes.closed_periods)
