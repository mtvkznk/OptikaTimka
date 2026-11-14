from datetime import date, time

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlmodel import Session, select

from app.models import Appointment, AppointmentStatus, AvailableTimeSlot, ServiceClosedDate

Interval = tuple[int, int]


class BookingTime(BaseModel):
    start_time: time
    end_time: time


def minute_of(value: time) -> int:
    return value.hour * 60 + value.minute


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
    occupied = []
    for start_time, duration in session.exec(statement):
        start = minute_of(start_time)
        end = start + duration + int(bool(start_time.second or start_time.microsecond))
        occupied.append((start, end))
    return sorted(occupied)


def check_booking_overlap(
    session: Session, day: date, start: int, duration: int, exclude_id: int | None = None,
) -> None:
    if any(
        start < booked_end and booked_start < start + duration
        for booked_start, booked_end in occupied_intervals(session, day, exclude_id)
    ):
        raise HTTPException(409, "Цей час уже зайнятий. Оберіть інший час.")


def check_reactivation(session: Session, appointment: Appointment) -> None:
    if appointment.preferred_date is not None and appointment.preferred_time is not None:
        check_booking_overlap(
            session, appointment.preferred_date, minute_of(appointment.preferred_time),
            appointment.duration_minutes, appointment.id,
        )


def available_starts(
    working: list[Interval], occupied: list[Interval], duration: int, earliest: int,
) -> list[BookingTime]:
    options: list[BookingTime] = []
    for start, end in working:
        cursor = max(start, earliest)
        for blocked_start, blocked_end in [*occupied, (end, end)]:
            last_start = min(end, blocked_start) - duration
            for candidate in range(cursor, last_start + 1, duration):
                options.append(BookingTime(
                    start_time=time(*divmod(candidate, 60)),
                    end_time=time(*divmod(candidate + duration, 60)),
                ))
            cursor = max(cursor, blocked_end)
            if cursor >= end:
                break
    return options
