"""Shared transactional appointment commands for the website and Telegram.

Controllers own commit/rollback; every mutation holds the same booking lock.
"""

from datetime import date, datetime, time

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.admin_audit import add_admin_audit
from app.booking import (
    Interval,
    booking_start_fits,
    check_booking_overlap,
    check_reactivation,
    first_future_minute,
    get_active_time_slots,
    lock_booking_writes,
    minute_of,
    service_is_closed,
    working_intervals,
)
from app.config import settings
from app.models import (
    Appointment,
    AppointmentStatus,
    BookingBlock,
    BookingService,
    ClosedDate,
    ServiceSchedule,
    ServiceWeekdaySchedule,
    utc_now,
)
from app.validation import normalize_phone

DOCTOR_SERVICE_NAMES = (
    "Комплексний огляд лікаря офтальмолога",
    "Комплексний огляд лікаря-офтальмолога",
    "Огляд лікаря офтальмолога",
    "Огляд лікаря-офтальмолога",
    "Огляд лікаря",
    "Консультація офтальмолога",
)


def get_active_services(session: Session) -> list[BookingService]:
    return list(
        session.exec(
            select(BookingService)
            .where(BookingService.is_active)
            .order_by(BookingService.sort_order, BookingService.id)
        ).all()
    )


def service_working_intervals(session: Session, service_id: int, day: date) -> list[Interval]:
    schedule: ServiceWeekdaySchedule | ServiceSchedule | None = session.get(
        ServiceWeekdaySchedule,
        (service_id, day.weekday()),
    )
    if schedule is None:
        schedule = session.get(ServiceSchedule, service_id)
    if schedule is not None:
        return [tuple(interval) for interval in schedule.intervals]
    return working_intervals(get_active_time_slots(session))


def bookable_weekdays(
    service: BookingService,
    week: list[list[Interval]],
    *,
    duration: int | None = None,
) -> int:
    duration = service.duration_minutes if duration is None else duration
    return service.weekdays_mask & sum(
        1 << day
        for day, intervals in enumerate(week)
        if any(end - start >= duration for start, end in intervals)
    )


def format_date(value: date | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%d.%m.%Y")


def format_time(value: time | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%H:%M")


def format_minute(minute: int) -> str:
    return "{:02d}:{:02d}".format(*divmod(minute, 60))


def diary_service(services: list[BookingService]) -> BookingService | None:
    configured_id = settings.doctor_diary_service_id
    if configured_id is not None:
        return next((service for service in services if service.id == configured_id), None)
    by_name = {service.name.strip().casefold(): service for service in services}
    for name in DOCTOR_SERVICE_NAMES:
        if service := by_name.get(name.casefold()):
            return service
    return None


def find_diary_day(
    weekday_mask: int,
    closed_days: set[date],
    day: date,
    *,
    step: int = 1,
    include_day: bool = True,
) -> date | None:
    first = day.toordinal() + (0 if include_day else step)
    stop = date.max.toordinal() + 1 if step > 0 else 0
    for ordinal in range(first, stop, step):
        candidate = date.fromordinal(ordinal)
        if candidate not in closed_days and weekday_mask & (1 << candidate.weekday()):
            return candidate
    return None


def appointment_belongs_to_service(visit: Appointment, service: BookingService) -> bool:
    if visit.service_id is None:
        return visit.service == service.name
    return visit.service_id == service.id


def form_int(form_data: dict[str, str], key: str, default: int = 0) -> int:
    try:
        return int(form_data.get(key, default))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Вкажіть коректне ціле число.") from exc


def form_date(form_data: dict[str, str], key: str) -> date:
    value = form_data.get(key, "")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Некоректна дата."
        ) from exc


def form_time(form_data: dict[str, str], key: str) -> time:
    value = form_data.get(key, "")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Некоректний час."
        ) from exc


def diary_slot_from_form(form_data: dict[str, str]) -> tuple[date, time]:
    day = form_date(form_data, "preferred_date")
    start_time = form_time(form_data, "preferred_time")
    if start_time.tzinfo or start_time.second or start_time.microsecond:
        raise HTTPException(422, "Вкажіть час у годинах і хвилинах.")
    return day, start_time


def require_diary_service(session: Session) -> BookingService:
    service = diary_service(get_active_services(session))
    if service is None:
        raise HTTPException(409, "Огляд лікаря зараз недоступний для запису.")
    return service


def check_diary_day(
    session: Session,
    service: BookingService,
    day: date,
    start: int,
    *,
    now: datetime,
) -> None:
    if start < first_future_minute(day, now):
        raise HTTPException(409, "Цей час уже минув. Оберіть інший час.")
    if (
        not service.is_available_on(day)
        or service_is_closed(session, service.id, day)
        or session.exec(select(ClosedDate.id).where(ClosedDate.closed_on == day)).first()
        is not None
    ):
        raise HTTPException(409, "Цей день закритий для запису на огляд лікаря.")


def appointment_contact(name: str, phone: str) -> tuple[str, str]:
    name, phone = name.strip(), phone.strip()
    if not name and not phone:
        raise HTTPException(422, "Введіть ім’я або контакт.")
    if "\0" in name or "\0" in phone:
        raise HTTPException(422, "Перевірте символи в імені та контакті.")
    return name, phone


def create_manual(
    session: Session, form_data: dict[str, str], *, now: datetime, actor: str, locked: bool = False
) -> Appointment:
    name, phone = appointment_contact(form_data.get("name", ""), form_data.get("phone", ""))
    day, start_time = diary_slot_from_form(form_data)
    start = minute_of(start_time)
    if not locked:
        lock_booking_writes(session)
    service = require_diary_service(session)
    if (
        "expected_service_id" in form_data
        and form_int(form_data, "expected_service_id") != service.id
    ):
        raise HTTPException(409, "Послуга в зошиті змінилася. Виберіть час ще раз.")
    check_diary_day(session, service, day, start, now=now)
    duration = service.duration_minutes
    from_notebook = "expected_duration_minutes" in form_data
    if from_notebook and form_int(form_data, "expected_duration_minutes") != duration:
        raise HTTPException(
            409, "Тривалість послуги змінилася. Оновіть зошит і виберіть час ще раз."
        )
    working = service_working_intervals(session, service.id, day)
    if not booking_start_fits(working, start, duration, on_grid=from_notebook):
        if from_notebook:
            raise HTTPException(409, "Години змінилися. Оновіть зошит і виберіть час ще раз.")
        raise HTTPException(409, "Для прийому не вистачає часу до кінця робочого проміжку.")
    check_booking_overlap(session, day, start, duration, service_id=service.id)
    initial_status = AppointmentStatus.confirmed if day == now.date() else AppointmentStatus.new
    visit = Appointment(
        name=name,
        phone=phone,
        phone_normalized=normalize_phone(phone),
        service=service.name,
        service_id=service.id,
        preferred_date=day,
        preferred_time=start_time,
        status=initial_status,
        duration_minutes=duration,
    )
    session.add(visit)
    session.flush()
    add_admin_audit(
        session,
        actor,
        "appointment_created",
        f"Прийом №{visit.id} · {visit.service} · {format_date(day)} {format_time(start_time)}",
    )
    return visit


def update_contact(
    session: Session, appointment_id: int, form_data: dict[str, str], *, actor: str,
) -> Appointment:
    lock_booking_writes(session)
    service = require_diary_service(session)
    visit = session.get(Appointment, appointment_id)
    if visit is None or not appointment_belongs_to_service(visit, service):
        raise HTTPException(404, "Запис у зошиті не знайдено.")
    if form_data.get("version") != visit.updated_at.isoformat():
        raise HTTPException(409, "Запис змінився. Оновіть зошит і повторіть дію.")

    name, phone = appointment_contact(
        form_data.get("name", visit.name), form_data.get("phone", visit.phone),
    )
    if "name" not in form_data:
        name = visit.name
    if "phone" not in form_data:
        phone = visit.phone
    if (name, phone) == (visit.name, visit.phone):
        return visit
    visit.name = name
    if phone != visit.phone:
        visit.phone = phone
        visit.phone_normalized = normalize_phone(phone)
    visit.updated_at = utc_now()
    add_admin_audit(
        session, actor, "appointment_contact_updated",
        f"Прийом №{visit.id} · {visit.service} · {format_date(visit.preferred_date)} {format_time(visit.preferred_time)}",
    )
    session.flush()
    session.refresh(visit)
    return visit


def change_block(
    session: Session, form_data: dict[str, str], *, now: datetime, actor: str, locked: bool = False
) -> None:
    day, start_time = diary_slot_from_form(form_data)
    start = minute_of(start_time)
    action = form_data.get("block_action")
    if action not in {"close", "open"}:
        raise HTTPException(422, "Некоректна дія для години запису.")

    if not locked:
        lock_booking_writes(session)
    service = require_diary_service(session)
    if form_int(form_data, "service_id") != service.id:
        raise HTTPException(409, "Послуга в зошиті змінилася. Оновіть сторінку.")
    block = session.get(BookingBlock, (service.id, day, start))
    if action == "open":
        if block is not None:
            add_admin_audit(
                session,
                actor,
                "slot_reopened",
                f"{service.name} · {format_date(day)} · {format_minute(start)}–{format_minute(block.end_minute)}",
            )
            session.delete(block)
    elif block is None:
        check_diary_day(session, service, day, start, now=now)
        duration = service.duration_minutes
        if form_int(form_data, "slot_duration") != duration or not booking_start_fits(
            service_working_intervals(session, service.id, day),
            start,
            duration,
            on_grid=True,
        ):
            raise HTTPException(409, "Години змінилися. Оновіть зошит і виберіть час ще раз.")
        check_booking_overlap(session, day, start, duration, service_id=service.id)
        session.add(
            BookingBlock(
                service_id=service.id,
                day=day,
                start_minute=start,
                end_minute=start + duration,
            )
        )
        add_admin_audit(
            session,
            actor,
            "slot_closed",
            f"{service.name} · {format_date(day)} · {format_minute(start)}–{format_minute(start + duration)}",
        )

    session.flush()


def move_appointment(
    session: Session,
    appointment_id: int,
    form_data: dict[str, str],
    *,
    now: datetime,
    actor: str,
    locked: bool = False,
) -> Appointment:
    day, start_time = diary_slot_from_form(form_data)
    start = minute_of(start_time)
    if not locked:
        lock_booking_writes(session)
    service = require_diary_service(session)
    visit = session.get(Appointment, appointment_id)
    if (
        visit is None
        or visit.status == AppointmentStatus.canceled
        or not appointment_belongs_to_service(visit, service)
    ):
        raise HTTPException(409, "Цей запис більше недоступний для перенесення.")
    if form_data.get("version") != visit.updated_at.isoformat():
        raise HTTPException(409, "Запис змінився. Оновіть зошит і спробуйте ще раз.")
    if (day, start_time) == (visit.preferred_date, visit.preferred_time):
        raise HTTPException(409, "Оберіть іншу годину або дату.")

    check_diary_day(session, service, day, start, now=now)
    duration = visit.duration_minutes
    if not booking_start_fits(
        service_working_intervals(session, service.id, day), start, duration, on_grid=True
    ):
        raise HTTPException(409, "Години змінилися. Оновіть зошит і виберіть час ще раз.")
    check_booking_overlap(session, day, start, duration, service_id=service.id, exclude_id=visit.id)
    previous = f"{format_date(visit.preferred_date)} {format_time(visit.preferred_time)}"
    new = f"{format_date(day)} {format_time(start_time)}"
    visit.preferred_date = day
    visit.preferred_time = start_time
    visit.updated_at = utc_now()
    add_admin_audit(
        session,
        actor,
        "appointment_moved",
        f"Прийом №{visit.id} · {visit.service} · {previous} → {new}",
    )
    session.flush()
    session.refresh(visit)
    return visit


def set_status(
    session: Session,
    appointment_id: int,
    form_data: dict[str, str],
    *,
    now: datetime,
    actor: str,
    locked: bool = False,
) -> Appointment:
    if not locked:
        lock_booking_writes(session)
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Запис не знайдено.")

    if "version" in form_data and form_data["version"] != appointment.updated_at.isoformat():
        raise HTTPException(409, "Запис змінився. Оновіть розклад і повторіть дію.")

    try:
        new_status = AppointmentStatus(form_data.get("status", ""))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некоректний статус.",
        ) from exc

    if (
        appointment.status == AppointmentStatus.canceled
        and new_status != AppointmentStatus.canceled
    ):
        check_reactivation(session, appointment)
    appointment.status = new_status
    appointment.updated_at = utc_now()
    session.add(appointment)
    session.flush()
    add_admin_audit(
        session,
        actor,
        "appointment_status",
        f"Прийом №{appointment.id} · {appointment.service} · {new_status.value}",
    )
    return appointment


def remove_appointment(
    session: Session,
    appointment_id: int,
    form_data: dict[str, str],
    *,
    now: datetime,
    actor: str,
    locked: bool = False,
) -> None:
    if not locked:
        lock_booking_writes(session)
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(404, "Запис не знайдено. Оновіть сторінку.")
    if form_data.get("version") != appointment.updated_at.isoformat():
        raise HTTPException(409, "Запис змінився. Оновіть сторінку й повторіть дію.")

    day = appointment.preferred_date
    starts_at = appointment.preferred_time
    upcoming = day is not None and (
        day > now.date() or (day == now.date() and (starts_at is None or starts_at > now.time()))
    )
    if upcoming and form_data.get("confirmed") != "true":
        raise HTTPException(409, {"confirmation_required": "upcoming_appointment_removal"})

    add_admin_audit(
        session,
        actor,
        "appointment_deleted",
        f"Прийом №{appointment.id} · {appointment.service} · {format_date(day)} {format_time(starts_at)}",
    )
    session.delete(appointment)
    session.flush()
