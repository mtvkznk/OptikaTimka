from datetime import datetime

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from app.booking import (
    booking_start_fits,
    get_active_time_slots,
    occupied_interval,
    weekday_schedules_by_service,
    weekly_working_intervals,
    working_intervals,
)
from app.models import (
    Appointment,
    AppointmentStatus,
    BookingService,
    ClosedDate,
    ServiceClosedDate,
    ServiceSchedule,
)


def appointments_outside_schedule(
    session: Session, now: datetime, *, include_canceled: bool = False,
) -> dict[int, dict[str, str]]:
    today = now.date()
    statement = select(Appointment).where(
        Appointment.preferred_time.is_not(None),
        or_(
            Appointment.preferred_date > today,
            and_(Appointment.preferred_date == today, Appointment.preferred_time > now.time()),
        ),
    ).order_by(Appointment.preferred_date, Appointment.preferred_time, Appointment.id)
    if not include_canceled:
        statement = statement.where(Appointment.status != AppointmentStatus.canceled)
    appointments = session.exec(statement).all()
    if not appointments:
        return {}

    services = {row.id: row for row in session.exec(select(BookingService).where(BookingService.is_active))}
    service_by_name = {row.name: row for row in services.values()}
    default = working_intervals(get_active_time_slots(session))
    schedules = {row.service_id: row for row in session.exec(select(ServiceSchedule))}
    overrides = weekday_schedules_by_service(session, list(services))
    hours = {
        service_id: weekly_working_intervals(
            default, schedules.get(service_id), overrides.get(service_id, {}),
        )
        for service_id in services
    }
    global_closures = set(session.exec(select(ClosedDate.closed_on).where(ClosedDate.closed_on >= today)))
    service_closures = {
        (service_id, day) for service_id, day in session.exec(
            select(ServiceClosedDate.service_id, ServiceClosedDate.closed_on)
            .where(ServiceClosedDate.closed_on >= today)
        )
    }
    outside = {}
    for visit in appointments:
        day = visit.preferred_date
        service = (
            services.get(visit.service_id) if visit.service_id is not None else service_by_name.get(visit.service)
        )
        start, end = occupied_interval(visit.preferred_time, visit.duration_minutes)
        if (
            service is None or day in global_closures or (service.id, day) in service_closures
            or not service.is_available_on(day)
            or not booking_start_fits(hours[service.id][day.weekday()], start, end - start, on_grid=False)
        ):
            outside[visit.id] = {
                "date": day.strftime("%d.%m.%Y"),
                "time": visit.preferred_time.strftime("%H:%M"),
                "service": service.name if service is not None else visit.service,
            }
    return outside
