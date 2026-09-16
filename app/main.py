from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import (
    AnalyticsEvent,
    AnalyticsEventCreate,
    AnalyticsEventRead,
    Appointment,
    AppointmentCreate,
    AppointmentRead,
    AppointmentStatus,
    AvailableTimeSlot,
    AvailableTimeSlotRead,
    BookingService,
    BookingServiceRead,
    ClosedDate,
    ClosedDateRead,
    ContentBlock,
    ContentBlockRead,
    Product,
    ProductCreate,
    ProductRead,
    utc_now,
)

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
SessionDep = Annotated[Session, Depends(get_session)]

DEFAULT_SERVICES = [
    {
        "name": "Консультація офтальмолога",
        "duration_minutes": 30,
        "price_uah": 500,
        "sort_order": 1,
    },
    {
        "name": "Перевірка зору",
        "duration_minutes": 30,
        "price_uah": 400,
        "sort_order": 2,
    },
    {
        "name": "Підбір окулярів",
        "duration_minutes": 45,
        "price_uah": 600,
        "sort_order": 3,
    },
    {
        "name": "Підбір контактних лінз",
        "duration_minutes": 30,
        "price_uah": 500,
        "sort_order": 4,
    },
]

DEFAULT_TIME_SLOTS = [
    {"start_time": time(9, 0), "end_time": time(10, 0), "sort_order": 1},
    {"start_time": time(10, 0), "end_time": time(11, 0), "sort_order": 2},
    {"start_time": time(11, 0), "end_time": time(12, 0), "sort_order": 3},
    {"start_time": time(12, 0), "end_time": time(13, 0), "sort_order": 4},
    {"start_time": time(14, 0), "end_time": time(15, 0), "sort_order": 5},
    {"start_time": time(15, 0), "end_time": time(16, 0), "sort_order": 6},
    {"start_time": time(16, 0), "end_time": time(17, 0), "sort_order": 7},
]

STATUS_OPTIONS = [
    {"value": AppointmentStatus.new.value, "label": "Новий"},
    {"value": AppointmentStatus.confirmed.value, "label": "Підтверджено"},
    {"value": AppointmentStatus.canceled.value, "label": "Скасовано"},
]

STATUS_LABELS = {option["value"]: option["label"] for option in STATUS_OPTIONS}
BOOKING_DATE_WINDOW_DAYS = 45
ASSET_VERSION = "20260916-service-prices"
MONTH_TITLE_LABELS = {
    1: "СІЧЕНЬ / JANUARY",
    2: "ЛЮТИЙ / FEBRUARY",
    3: "БЕРЕЗЕНЬ / MARCH",
    4: "КВІТЕНЬ / APRIL",
    5: "ТРАВЕНЬ / MAY",
    6: "ЧЕРВЕНЬ / JUNE",
    7: "ЛИПЕНЬ / JULY",
    8: "СЕРПЕНЬ / AUGUST",
    9: "ВЕРЕСЕНЬ / SEPTEMBER",
    10: "ЖОВТЕНЬ / OCTOBER",
    11: "ЛИСТОПАД / NOVEMBER",
    12: "ГРУДЕНЬ / DECEMBER",
}
WEEKDAY_LABELS = [
    {"uk": "Пн", "en": "Mon", "weekend": False},
    {"uk": "Вт", "en": "Tue", "weekend": False},
    {"uk": "Ср", "en": "Wed", "weekend": False},
    {"uk": "Чт", "en": "Thu", "weekend": False},
    {"uk": "Пт", "en": "Fri", "weekend": False},
    {"uk": "Сб", "en": "Sat", "weekend": True},
    {"uk": "Нд", "en": "Sun", "weekend": True},
]


class ReorderPayload(BaseModel):
    ids: list[int]


def ensure_seed_data(session: Session) -> None:
    for service_data in DEFAULT_SERVICES:
        service = session.exec(
            select(BookingService).where(BookingService.name == service_data["name"])
        ).first()
        if service is None:
            session.add(BookingService(**service_data))

    for slot_data in DEFAULT_TIME_SLOTS:
        slot = session.exec(
            select(AvailableTimeSlot).where(
                AvailableTimeSlot.start_time == slot_data["start_time"]
            )
        ).first()
        if slot is None:
            session.add(AvailableTimeSlot(**slot_data))

    session.commit()


def get_active_services(session: Session) -> list[BookingService]:
    return list(
        session.exec(
            select(BookingService)
            .where(BookingService.is_active)
            .order_by(BookingService.sort_order, BookingService.id)
        ).all()
    )


def get_active_time_slots(session: Session) -> list[AvailableTimeSlot]:
    return list(
        session.exec(
            select(AvailableTimeSlot)
            .where(AvailableTimeSlot.is_active)
            .order_by(AvailableTimeSlot.sort_order, AvailableTimeSlot.id)
        ).all()
    )


def count_rows(session: Session, model: type[Any], *conditions: Any) -> int:
    statement = select(func.count()).select_from(model)
    for condition in conditions:
        statement = statement.where(condition)
    return int(session.exec(statement).one())


def status_value(appointment: Appointment) -> str:
    if isinstance(appointment.status, AppointmentStatus):
        return appointment.status.value
    return str(appointment.status)


def status_label(value: AppointmentStatus | str) -> str:
    key = value.value if isinstance(value, AppointmentStatus) else str(value)
    return STATUS_LABELS.get(key, key)


def format_date(value: date | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%d.%m.%Y")


def format_time(value: time | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%H:%M")


def current_date() -> date:
    return datetime.now(UTC).date()


def build_appointment_rows(appointments: list[Appointment]) -> list[dict[str, Any]]:
    return [
        {
            "appointment": appointment,
            "status_value": status_value(appointment),
            "status_label": status_label(appointment.status),
        }
        for appointment in appointments
    ]


def get_available_booking_dates(session: Session) -> list[dict[str, str]]:
    today = current_date()
    closed_dates = set(
        session.exec(
            select(ClosedDate.closed_on).where(ClosedDate.closed_on >= today)
        ).all()
    )
    available_dates = []

    for offset in range(BOOKING_DATE_WINDOW_DAYS):
        day = today + timedelta(days=offset)
        if day in closed_dates:
            continue

        available_dates.append(
            {
                "value": day.isoformat(),
                "label": format_date(day),
            }
        )

    return available_dates


def next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def build_booking_calendar(session: Session) -> list[dict[str, Any]]:
    today = current_date()
    window_end = today + timedelta(days=BOOKING_DATE_WINDOW_DAYS - 1)
    closed_dates = set(
        session.exec(
            select(ClosedDate.closed_on).where(ClosedDate.closed_on >= today)
        ).all()
    )
    months: list[dict[str, Any]] = []
    month_start = date(today.year, today.month, 1)

    while month_start <= window_end:
        month_end = next_month(month_start) - timedelta(days=1)
        grid_start = month_start - timedelta(days=month_start.weekday())
        grid_end = month_end + timedelta(days=6 - month_end.weekday())
        weeks: list[list[dict[str, Any]]] = []
        week: list[dict[str, Any]] = []
        cursor = grid_start

        while cursor <= grid_end:
            in_month = cursor.month == month_start.month
            is_available = (
                in_month
                and today <= cursor <= window_end
                and cursor not in closed_dates
            )
            week.append(
                {
                    "value": cursor.isoformat(),
                    "label": format_date(cursor),
                    "day": str(cursor.day),
                    "is_available": is_available,
                    "is_today": cursor == today,
                    "is_weekend": cursor.weekday() >= 5,
                    "is_outside_month": not in_month,
                }
            )

            if len(week) == 7:
                weeks.append(week)
                week = []

            cursor += timedelta(days=1)

        months.append(
            {
                "title": f"{MONTH_TITLE_LABELS[month_start.month]} {month_start.year}",
                "weeks": weeks,
            }
        )
        month_start = next_month(month_start)

    return months


def admin_return_url(form_data: dict[str, str], fallback: str = "/admin#appointments") -> str:
    return_to = form_data.get("return_to", fallback)
    if not return_to.startswith("/admin"):
        return fallback
    return return_to


def apply_sort_order(session: Session, model: type[Any], ids: list[int]) -> None:
    seen_ids = set()
    for sort_order, item_id in enumerate(ids, start=1):
        if item_id in seen_ids:
            continue

        seen_ids.add(item_id)
        item = session.get(model, item_id)
        if item is None:
            continue

        item.sort_order = sort_order
        item.updated_at = utc_now()
        session.add(item)

    session.commit()


def admin_context(session: Session, request: Request) -> dict[str, Any]:
    ensure_seed_data(session)
    today = current_date()
    week_end = today + timedelta(days=7)

    services = list(
        session.exec(select(BookingService).order_by(BookingService.sort_order, BookingService.id)).all()
    )
    time_slots = list(
        session.exec(
            select(AvailableTimeSlot).order_by(AvailableTimeSlot.sort_order, AvailableTimeSlot.id)
        ).all()
    )
    closed_dates = list(
        session.exec(select(ClosedDate).order_by(ClosedDate.closed_on, ClosedDate.id)).all()
    )
    latest_appointments = list(
        session.exec(select(Appointment).order_by(Appointment.created_at.desc()).limit(8)).all()
    )

    active_time_slots = [slot for slot in time_slots if slot.is_active]
    active_services = [service for service in services if service.is_active]
    closed_in_week = {
        item.closed_on
        for item in closed_dates
        if today <= item.closed_on < week_end
    }
    workdays = [
        today + timedelta(days=offset)
        for offset in range(7)
        if (today + timedelta(days=offset)).weekday() < 5
        and (today + timedelta(days=offset)) not in closed_in_week
    ]

    return {
        "request": request,
        "app_name": settings.app_name,
        "asset_version": ASSET_VERSION,
        "services": services,
        "time_slots": time_slots,
        "closed_dates": closed_dates,
        "status_options": STATUS_OPTIONS,
        "appointment_rows": build_appointment_rows(latest_appointments),
        "metrics": {
            "today_appointments": count_rows(
                session,
                Appointment,
                Appointment.preferred_date == today,
            ),
            "planned_appointments": count_rows(
                session,
                Appointment,
                Appointment.preferred_date >= today,
            ),
            "active_services": len(active_services),
            "all_services": len(services),
            "available_hours": len(workdays) * len(active_time_slots),
        },
        "format_date": format_date,
        "format_time": format_time,
    }


def appointments_context(session: Session, request: Request) -> dict[str, Any]:
    ensure_seed_data(session)
    appointments = list(
        session.exec(select(Appointment).order_by(Appointment.created_at.desc())).all()
    )

    return {
        "request": request,
        "app_name": settings.app_name,
        "asset_version": ASSET_VERSION,
        "status_options": STATUS_OPTIONS,
        "appointment_rows": build_appointment_rows(appointments),
        "format_date": format_date,
        "format_time": format_time,
    }


async def read_form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def form_int(form_data: dict[str, str], key: str, default: int = 0) -> int:
    try:
        return int(form_data.get(key, default))
    except (TypeError, ValueError):
        return default


def form_date(form_data: dict[str, str], key: str) -> date:
    value = form_data.get(key, "")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некоректна дата.") from exc


def form_time(form_data: dict[str, str], key: str) -> time:
    value = form_data.get(key, "")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некоректний час.") from exc


@app.get("/", response_class=HTMLResponse)
def root(request: Request, session: SessionDep) -> HTMLResponse:
    ensure_seed_data(session)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "asset_version": ASSET_VERSION,
            "services": get_active_services(session),
            "time_slots": get_active_time_slots(session),
            "booking_months": build_booking_calendar(session),
            "weekday_labels": WEEKDAY_LABELS,
            "format_time": format_time,
            "today": current_date().isoformat(),
        },
    )


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin.html", admin_context(session, request))


@app.get("/admin/appointments", response_class=HTMLResponse)
def admin_appointments(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin_appointments.html",
        appointments_context(session, request),
    )


@app.get("/api/status")
def api_status() -> dict[str, object]:
    return {
        "app": settings.app_name,
        "status": "backend-ready",
        "stack": ["FastAPI", "SQLModel", "Alembic", "PostgreSQL"],
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/api/content-blocks", response_model=list[ContentBlockRead])
def list_content_blocks(session: SessionDep) -> list[ContentBlock]:
    return list(
        session.exec(
            select(ContentBlock)
            .where(ContentBlock.is_visible)
            .order_by(ContentBlock.sort_order, ContentBlock.id)
        ).all()
    )


@app.get("/api/booking-services", response_model=list[BookingServiceRead])
def list_booking_services(session: SessionDep) -> list[BookingService]:
    ensure_seed_data(session)
    return get_active_services(session)


@app.get("/api/time-slots", response_model=list[AvailableTimeSlotRead])
def list_time_slots(session: SessionDep) -> list[AvailableTimeSlot]:
    ensure_seed_data(session)
    return get_active_time_slots(session)


@app.get("/api/closed-dates", response_model=list[ClosedDateRead])
def list_closed_dates(session: SessionDep) -> list[ClosedDate]:
    return list(session.exec(select(ClosedDate).order_by(ClosedDate.closed_on, ClosedDate.id)).all())


@app.get("/api/products", response_model=list[ProductRead])
def list_products(session: SessionDep) -> list[Product]:
    return list(
        session.exec(
            select(Product)
            .where(Product.is_active)
            .order_by(Product.sort_order, Product.id)
        ).all()
    )


@app.post("/api/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, session: SessionDep) -> Product:
    product = Product.model_validate(payload)
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


@app.post("/api/appointments", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
def create_appointment(payload: AppointmentCreate, session: SessionDep) -> Appointment:
    ensure_seed_data(session)

    service = session.exec(
        select(BookingService)
        .where(BookingService.name == payload.service)
        .where(BookingService.is_active)
    ).first()
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Оберіть активну послугу.",
        )

    if payload.preferred_time is not None:
        slot = session.exec(
            select(AvailableTimeSlot)
            .where(AvailableTimeSlot.start_time == payload.preferred_time)
            .where(AvailableTimeSlot.is_active)
        ).first()
        if slot is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Оберіть доступний час.",
            )

    if payload.preferred_date is not None:
        if payload.preferred_date < current_date():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Оберіть майбутню дату.",
            )

        closed_date = session.exec(
            select(ClosedDate).where(ClosedDate.closed_on == payload.preferred_date)
        ).first()
        if closed_date is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="На цю дату запис закритий.",
            )

    appointment = Appointment.model_validate(payload)
    session.add(appointment)
    session.commit()
    session.refresh(appointment)
    return appointment


@app.post(
    "/api/analytics/events",
    response_model=AnalyticsEventRead,
    status_code=status.HTTP_201_CREATED,
)
def record_analytics_event(
    payload: AnalyticsEventCreate,
    session: SessionDep,
) -> AnalyticsEvent:
    event = AnalyticsEvent.model_validate(payload)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


@app.post("/admin/appointments/{appointment_id}/status")
async def update_appointment_status(
    appointment_id: int,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Запис не знайдено.")

    form_data = await read_form_data(request)
    try:
        appointment.status = AppointmentStatus(form_data.get("status", ""))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некоректний статус.",
        ) from exc

    appointment.updated_at = utc_now()
    session.add(appointment)
    session.commit()
    return RedirectResponse(url=admin_return_url(form_data), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/services")
async def create_service(request: Request, session: SessionDep) -> RedirectResponse:
    form_data = await read_form_data(request)
    service = BookingService(
        name=form_data.get("name", "").strip(),
        duration_minutes=form_int(form_data, "duration_minutes", 30),
        price_uah=form_int(form_data, "price_uah", 0),
        sort_order=form_int(form_data, "sort_order", 0),
        is_active=form_data.get("is_active") == "on",
    )
    if not service.name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Вкажіть назву послуги.")

    session.add(service)
    session.commit()
    return RedirectResponse(url="/admin#services", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/reorder/services")
def reorder_services(payload: ReorderPayload, session: SessionDep) -> dict[str, str]:
    apply_sort_order(session, BookingService, payload.ids)
    return {"status": "ok"}


@app.post("/admin/services/{service_id}")
async def update_service(
    service_id: int,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    service = session.get(BookingService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Послугу не знайдено.")

    form_data = await read_form_data(request)
    service.name = form_data.get("name", "").strip() or service.name
    service.duration_minutes = form_int(form_data, "duration_minutes", service.duration_minutes)
    service.price_uah = form_int(form_data, "price_uah", service.price_uah)
    service.sort_order = form_int(form_data, "sort_order", service.sort_order)
    service.is_active = form_data.get("is_active") == "on"
    service.updated_at = utc_now()
    session.add(service)
    session.commit()
    return RedirectResponse(url="/admin#services", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/time-slots")
async def create_time_slot(request: Request, session: SessionDep) -> RedirectResponse:
    form_data = await read_form_data(request)
    slot = AvailableTimeSlot(
        start_time=form_time(form_data, "start_time"),
        end_time=form_time(form_data, "end_time"),
        sort_order=form_int(form_data, "sort_order", 0),
        is_active=form_data.get("is_active") == "on",
    )
    session.add(slot)
    session.commit()
    return RedirectResponse(url="/admin#time-slots", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/reorder/time-slots")
def reorder_time_slots(payload: ReorderPayload, session: SessionDep) -> dict[str, str]:
    apply_sort_order(session, AvailableTimeSlot, payload.ids)
    return {"status": "ok"}


@app.post("/admin/time-slots/{slot_id}")
async def update_time_slot(
    slot_id: int,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    slot = session.get(AvailableTimeSlot, slot_id)
    if slot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Годину не знайдено.")

    form_data = await read_form_data(request)
    slot.start_time = form_time(form_data, "start_time")
    slot.end_time = form_time(form_data, "end_time")
    slot.sort_order = form_int(form_data, "sort_order", slot.sort_order)
    slot.is_active = form_data.get("is_active") == "on"
    slot.updated_at = utc_now()
    session.add(slot)
    session.commit()
    return RedirectResponse(url="/admin#time-slots", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/closed-dates")
async def create_closed_date(request: Request, session: SessionDep) -> RedirectResponse:
    form_data = await read_form_data(request)
    closed_date = ClosedDate(
        closed_on=form_date(form_data, "closed_on"),
        reason=form_data.get("reason", "").strip(),
    )
    session.add(closed_date)
    session.commit()
    return RedirectResponse(url="/admin#closed-dates", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/closed-dates/{closed_date_id}")
async def update_closed_date(
    closed_date_id: int,
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    closed_date = session.get(ClosedDate, closed_date_id)
    if closed_date is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Закриту дату не знайдено.")

    form_data = await read_form_data(request)
    closed_date.closed_on = form_date(form_data, "closed_on")
    closed_date.reason = form_data.get("reason", "").strip()
    closed_date.updated_at = utc_now()
    session.add(closed_date)
    session.commit()
    return RedirectResponse(url="/admin#closed-dates", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/closed-dates/{closed_date_id}/delete")
def delete_closed_date(closed_date_id: int, session: SessionDep) -> RedirectResponse:
    closed_date = session.get(ClosedDate, closed_date_id)
    if closed_date is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Закриту дату не знайдено.")

    session.delete(closed_date)
    session.commit()
    return RedirectResponse(url="/admin#closed-dates", status_code=status.HTTP_303_SEE_OTHER)
