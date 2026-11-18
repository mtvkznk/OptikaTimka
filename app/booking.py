from collections.abc import Iterator
from datetime import date, datetime, time

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlmodel import Session, select

from app.models import (
    Appointment,
    AppointmentStatus,
    AvailableTimeSlot,
    BookingBlock,
    BookingService,
    ServiceClosedDate,
)

Interval = tuple[int, int]


class BookingTime(BaseModel):
    start_time: time
    end_time: time


def minute_of(value: time) -> int:
    return value.hour * 60 + value.minute


def first_future_minute(day: date, now: datetime) -> int:
    if day < now.date():
        return 24 * 60
    return minute_of(now.time()) + 1 if day == now.date() else 0


def occupied_interval(start_time: time, duration: int) -> Interval:
    start = minute_of(start_time)
    end = start + duration + int(bool(start_time.second or start_time.microsecond))
    return start, end


def lock_booking_writes(session: Session) -> None:
    # Keep the overlap check and write in one transaction, across app processes.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(select(func.pg_advisory_xact_lock(714004308)))
    else:
        session.execute(text("BEGIN IMMEDIATE"))


def working_intervals(slots: list[AvailableTimeSlot]) -> list[Interval]:
    intervals: list[Interval] = []
    for start, end in sorted(
        (minute_of(slot.start_time), minute_of(slot.end_time)) for slot in slots
    ):
        if intervals and start <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(end, intervals[-1][1]))
        else:
            intervals.append((start, end))
    return intervals


def service_is_closed(session: Session, service_id: int, day: date) -> bool:
    return session.exec(
        select(ServiceClosedDate.id).where(
            ServiceClosedDate.service_id == service_id, ServiceClosedDate.closed_on == day,
        )
    ).first() is not None


def occupied_intervals(session: Session, day: date, exclude_id: int | None = None) -> list[Interval]:
    statement = select(Appointment.preferred_time, Appointment.duration_minutes).where(
        Appointment.preferred_date == day,
        Appointment.status != AppointmentStatus.canceled,
        Appointment.preferred_time.is_not(None),
    )
    if exclude_id is not None:
        statement = statement.where(Appointment.id != exclude_id)
    return sorted(
        occupied_interval(start_time, duration) for start_time, duration in session.exec(statement)
    )


def blocked_intervals(session: Session, service_id: int, day: date) -> list[Interval]:
    rows = session.exec(
        select(BookingBlock.start_minute, BookingBlock.end_minute)
        .where(BookingBlock.service_id == service_id, BookingBlock.day == day)
    )
    return [(start, end) for start, end in rows]


def overlaps(start: int, end: int, intervals: list[Interval]) -> bool:
    return any(start < other_end and other_start < end for other_start, other_end in intervals)


def check_booking_overlap(
    session: Session, day: date, start: int, duration: int, *,
    service_id: int | None, exclude_id: int | None = None,
) -> None:
    end = start + duration
    if overlaps(start, end, occupied_intervals(session, day, exclude_id)):
        raise HTTPException(409, "Цей час уже зайнятий. Оберіть інший час.")
    if service_id is not None and overlaps(start, end, blocked_intervals(session, service_id, day)):
        raise HTTPException(409, "Цей час закритий для запису. Оберіть інший час.")


def check_reactivation(session: Session, appointment: Appointment) -> None:
    if appointment.preferred_date is not None and appointment.preferred_time is not None:
        start, end = occupied_interval(appointment.preferred_time, appointment.duration_minutes)
        service_id = appointment.service_id
        if service_id is None:
            service_id = session.exec(
                select(BookingService.id).where(BookingService.name == appointment.service)
            ).first()
        check_booking_overlap(
            session, appointment.preferred_date, start, end - start,
            service_id=service_id, exclude_id=appointment.id,
        )


def booking_start_fits(
    working: list[Interval], start: int, duration: int, *, on_grid: bool,
) -> bool:
    return any(
        opens <= start and start + duration <= closes
        and (not on_grid or (start - opens) % duration == 0)
        for opens, closes in working
    )


def available_start_minutes(
    working: list[Interval], occupied: list[Interval], duration: int, earliest: int,
) -> Iterator[int]:
    for opens, closes in working:
        # Round forward on the working interval's grid, including on the current day.
        skipped_slots = max(0, (earliest - opens + duration - 1) // duration)
        for start in range(opens + skipped_slots * duration, closes - duration + 1, duration):
            if not overlaps(start, start + duration, occupied):
                yield start


def available_starts(
    working: list[Interval], occupied: list[Interval], duration: int, earliest: int,
) -> list[BookingTime]:
    return [
        BookingTime(start_time=time(*divmod(start, 60)), end_time=time(*divmod(start + duration, 60)))
        for start in available_start_minutes(working, occupied, duration, earliest)
    ]
