import base64
import hashlib
import re
import secrets
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import and_, delete, func, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.admin_changes import AdminChanges, apply_admin_changes, closed_periods_for_display
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
    AvailableTimeSlotCreate,
    AvailableTimeSlotRead,
    BookingService,
    BookingServiceCreate,
    BookingServiceRead,
    ClosedDate,
    ClosedDateCreate,
    ClosedDateRead,
    ContentBlock,
    ContentBlockRead,
    Customer,
    CustomerSession,
    PhoneAuthCode,
    Product,
    ProductCreate,
    ProductRead,
    utc_now,
)
from app.security import (
    GOOGLE_SESSION_PREFIX,
    SESSION_COOKIE_NAME,
    CanonicalHostMiddleware,
    SecurityMiddleware,
    cookie_name,
    get_customer_by_session,
    hash_secret,
    hash_session_token,
    is_google_admin,
    rate_limit,
    read_form_data,
    require_admin,
    utc_expired,
)
from app.validation import normalize_email, normalize_phone

IS_PRODUCTION = settings.environment == "production"
app = FastAPI(
    title=settings.app_name,
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)
allowed_hosts = ["localhost", "127.0.0.1", "[::1]"]
if not IS_PRODUCTION:
    allowed_hosts.append("testserver")
if settings.public_base_url:
    allowed_hosts.append(urlsplit(settings.public_base_url).hostname)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
app.add_middleware(CanonicalHostMiddleware)
app.add_middleware(SecurityMiddleware)
admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
SessionDep = Annotated[Session, Depends(get_session)]
FormDataDep = Annotated[dict[str, str], Depends(read_form_data)]


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
    return JSONResponse(
        {"detail": "Цей час уже зайнятий або такі дані вже існують. Оновіть сторінку."},
        status_code=409,
    )


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
KYIV_TZ = ZoneInfo("Europe/Kyiv")
BOOKING_MAX_DAYS_AHEAD = 30
BOOKING_DAILY_RELEASE_TIME = time(11, 0)
ASSET_VERSION = "20261111-cabinet-admin"
ADMIN_ASSET_VERSION = "20261111-admin-changes"
OAUTH_STATE_COOKIE_NAME = "optika_oauth_state"
OAUTH_VERIFIER_COOKIE_NAME = "optika_oauth_verifier"
SESSION_TTL_DAYS = 30
PHONE_CODE_TTL_MINUTES = 10
PHONE_CODE_MAX_ATTEMPTS = 5
MONTH_TITLE_LABELS = {
    1: "Січень",
    2: "Лютий",
    3: "Березень",
    4: "Квітень",
    5: "Травень",
    6: "Червень",
    7: "Липень",
    8: "Серпень",
    9: "Вересень",
    10: "Жовтень",
    11: "Листопад",
    12: "Грудень",
}
WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


class ReorderPayload(BaseModel):
    ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=1000)


def secure_cookie_enabled() -> bool:
    return settings.environment.lower() == "production"


def hash_phone_code(phone: str, code: str) -> str:
    return hash_secret(f"phone-code:{phone}:{code}")


def google_auth_enabled() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def ensure_seed_data(session: Session) -> None:
    # Defaults initialize empty tables. Administrators own their contents after that.
    services = session.exec(select(BookingService.id).limit(1)).first() is None
    slots = session.exec(select(AvailableTimeSlot.id).limit(1)).first() is None
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    if services:
        session.execute(
            insert(BookingService)
            .values(DEFAULT_SERVICES)
            .on_conflict_do_nothing(
                index_elements=[BookingService.name],
            )
        )
    if slots:
        session.execute(
            insert(AvailableTimeSlot)
            .values(DEFAULT_TIME_SLOTS)
            .on_conflict_do_nothing(
                index_elements=[AvailableTimeSlot.start_time],
            )
        )
    if services or slots:
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


def kyiv_now() -> datetime:
    return datetime.now(KYIV_TZ)


def current_date() -> date:
    return kyiv_now().date()


def booking_window(now: datetime | None = None) -> tuple[date, date]:
    now_kyiv = kyiv_now() if now is None else now.astimezone(KYIV_TZ)
    today = now_kyiv.date()
    release_anchor = (
        today if now_kyiv.time() >= BOOKING_DAILY_RELEASE_TIME else today - timedelta(days=1)
    )
    return today, release_anchor + timedelta(days=BOOKING_MAX_DAYS_AHEAD)


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
    today, window_end = booking_window()
    closed_dates = set(
        session.exec(
            select(ClosedDate.closed_on)
            .where(ClosedDate.closed_on >= today)
            .where(ClosedDate.closed_on <= window_end)
        ).all()
    )
    available_dates = []

    for offset in range((window_end - today).days + 1):
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
    today, window_end = booking_window()
    closed_dates = set(
        session.exec(
            select(ClosedDate.closed_on)
            .where(ClosedDate.closed_on >= today)
            .where(ClosedDate.closed_on <= window_end)
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
            is_available = in_month and today <= cursor <= window_end and cursor not in closed_dates
            week.append(
                {
                    "value": cursor.isoformat(),
                    "label": format_date(cursor),
                    "day": str(cursor.day),
                    "is_available": is_available,
                    "is_today": cursor == today,
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
    try:
        parsed = urlsplit(return_to)
    except ValueError:
        return fallback
    if parsed.scheme or parsed.netloc or parsed.path not in {"/admin", "/admin/appointments"}:
        return fallback
    return return_to


def apply_sort_order(session: Session, model: type[Any], ids: list[int]) -> None:
    items = {item.id: item for item in session.exec(select(model)).all()}
    if len(ids) != len(items) or set(ids) != items.keys():
        raise HTTPException(409, "Список змінився. Оновіть сторінку й повторіть дію.")
    for order, item_id in enumerate(ids, start=1):
        item = items[item_id]
        item.sort_order = order
        item.updated_at = utc_now()
        session.add(item)

    session.commit()


def admin_context(session: Session, request: Request) -> dict[str, Any]:
    ensure_seed_data(session)
    today = current_date()

    services = list(
        session.exec(
            select(BookingService).order_by(BookingService.sort_order, BookingService.id)
        ).all()
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
        session.exec(
            select(Appointment)
            .order_by(Appointment.created_at.desc(), Appointment.id.desc())
            .limit(8)
        ).all()
    )

    active_time_slots = [slot for slot in time_slots if slot.is_active]
    active_services = [service for service in services if service.is_active]

    return {
        "request": request,
        "app_name": settings.app_name,
        "admin_asset_version": ADMIN_ASSET_VERSION,
        "today": today,
        "services": services,
        "time_slots": time_slots,
        "closed_periods": closed_periods_for_display(closed_dates),
        "status_options": STATUS_OPTIONS,
        "appointment_rows": build_appointment_rows(latest_appointments),
        "appointment_return_url": "/admin#appointments",
        "metrics": {
            "today_appointments": count_rows(
                session,
                Appointment,
                Appointment.preferred_date == today,
            ),
            "new_appointments": count_rows(
                session,
                Appointment,
                Appointment.status == AppointmentStatus.new,
            ),
            "active_services": len(active_services),
            "all_services": len(services),
            "active_time_slots": len(active_time_slots),
        },
        "format_date": format_date,
        "format_time": format_time,
    }


def appointments_context(
    session: Session,
    request: Request,
    page: int,
    q: str,
    visit_date: date | None,
    status_filter: AppointmentStatus | Literal[""],
) -> dict[str, Any]:
    filters = {}
    conditions = []
    if q:
        filters["q"] = q
        conditions.append(
            or_(
                Appointment.name.icontains(q, autoescape=True),
                Appointment.phone.icontains(q, autoescape=True),
                Appointment.phone_normalized.icontains(q, autoescape=True),
            )
        )
    if visit_date:
        filters["day"] = visit_date.isoformat()
        conditions.append(Appointment.preferred_date == visit_date)
    if status_filter:
        filters["status"] = status_filter.value
        conditions.append(Appointment.status == status_filter)

    total = count_rows(session, Appointment, *conditions)
    pages = max(1, (total + 49) // 50)
    page = min(page, pages)
    appointments = list(
        session.exec(
            select(Appointment)
            .where(*conditions)
            .order_by(Appointment.created_at.desc(), Appointment.id.desc())
            .offset((page - 1) * 50)
            .limit(50)
        ).all()
    )

    query = urlencode(filters)
    path = "/admin/appointments"
    current_query = urlencode({**filters, **({"page": page} if page > 1 else {})})
    return {
        "request": request,
        "app_name": settings.app_name,
        "admin_asset_version": ADMIN_ASSET_VERSION,
        "status_options": STATUS_OPTIONS,
        "appointment_rows": build_appointment_rows(appointments),
        "total_appointments": total,
        "appointment_return_url": f"{path}?{current_query}" if current_query else path,
        "filter_values": filters,
        "pagination_query": f"&{query}" if query else "",
        "page": page,
        "pages": pages,
        "format_date": format_date,
        "format_time": format_time,
    }


def form_int(form_data: dict[str, str], key: str, default: int = 0) -> int:
    try:
        return int(form_data.get(key, default))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Вкажіть коректне ціле число.") from exc


def validated_form(model: type[Any], values: dict[str, Any]) -> Any:
    try:
        return model.model_validate(values)
    except ValidationError as exc:
        raise HTTPException(422, "Перевірте значення та довжину полів форми.") from exc


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


def issue_customer_session(
    customer: Customer,
    session: Session,
    response: RedirectResponse,
    request: Request,
    *,
    via_google: bool = False,
) -> None:
    clear_customer_session(request, session, response)
    session.execute(delete(CustomerSession).where(CustomerSession.expires_at < utc_now()))
    token = secrets.token_urlsafe(32)
    if via_google:
        token = GOOGLE_SESSION_PREFIX + token
    session.add(
        CustomerSession(
            customer_id=customer.id or 0,
            token_hash=hash_session_token(token),
            expires_at=utc_now() + timedelta(days=SESSION_TTL_DAYS),
        )
    )
    session.commit()
    response.set_cookie(
        cookie_name(SESSION_COOKIE_NAME),
        token,
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=secure_cookie_enabled(),
        samesite="lax",
    )


def clear_customer_session(request: Request, session: Session, response: RedirectResponse) -> None:
    token = request.cookies.get(cookie_name(SESSION_COOKIE_NAME))
    if token:
        customer_session = session.exec(
            select(CustomerSession).where(CustomerSession.token_hash == hash_session_token(token))
        ).first()
        if customer_session is not None:
            session.delete(customer_session)
            session.commit()

    response.delete_cookie(
        cookie_name(SESSION_COOKIE_NAME),
        secure=secure_cookie_enabled(),
        httponly=True,
        samesite="lax",
    )


def upsert_phone_customer(session: Session, phone: str, name: str = "") -> Customer:
    customer = session.exec(select(Customer).where(Customer.phone == phone)).first()
    if customer is not None and customer.google_sub and not customer.phone_verified:
        customer.phone = None
        session.add(customer)
        session.flush()
        customer = None
    if customer is None:
        customer = Customer(phone=phone, name=name)
    elif name and not customer.name:
        customer.name = name

    if not customer.name:
        appointment = session.exec(
            select(Appointment)
            .where(Appointment.customer_id.is_(None), Appointment.phone_normalized == phone)
            .order_by(Appointment.created_at.desc())
            .limit(1)
        ).first()
        if appointment is not None:
            customer.name = appointment.name

    customer.phone_verified = True
    customer.last_login_at = utc_now()
    customer.updated_at = utc_now()
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


def upsert_google_customer(session: Session, profile: dict[str, Any]) -> Customer:
    google_sub = str(profile.get("sub") or "")
    email = normalize_email(str(profile.get("email") or ""))
    name = str(profile.get("name") or "").strip()[:160]
    if (
        not google_sub
        or len(google_sub) > 255
        or not email
        or len(email) > 255
        or profile.get("email_verified") is not True
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Google не повернув профіль."
        )

    customer = session.exec(select(Customer).where(Customer.google_sub == google_sub)).first()
    email_customer = session.exec(select(Customer).where(Customer.email == email)).first()
    if customer is None and email_customer is not None and email_customer.email_verified:
        if email_customer.google_sub and email_customer.google_sub != google_sub:
            raise HTTPException(409, "Не вдалося підтвердити обліковий запис.")
        customer = email_customer
    if email_customer is not None and email_customer is not customer:
        if email_customer.email_verified:
            raise HTTPException(409, "Не вдалося підтвердити обліковий запис.")
        email_customer.email = None
        session.add(email_customer)
        session.flush()

    if customer is None:
        customer = Customer(google_sub=google_sub, email=email, name=name)
    else:
        customer.google_sub = customer.google_sub or google_sub
        customer.email = email
        if name:
            customer.name = name

    customer.email_verified = True
    customer.last_login_at = utc_now()
    customer.updated_at = utc_now()
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


def customer_appointment_filter(customer: Customer) -> Any:
    verified_contacts = []
    if customer.phone_verified and customer.phone:
        verified_contacts.append(Appointment.phone_normalized == customer.phone)
    if customer.email_verified and customer.email:
        verified_contacts.append(Appointment.email_normalized == customer.email)
    owned = Appointment.customer_id == customer.id
    if verified_contacts:
        return or_(owned, and_(Appointment.customer_id.is_(None), or_(*verified_contacts)))
    return owned


def customer_appointments(customer: Customer, session: Session, page: int = 1) -> list[Appointment]:
    return list(
        session.exec(
            select(Appointment)
            .where(customer_appointment_filter(customer))
            .order_by(Appointment.created_at.desc(), Appointment.id.desc())
            .offset((page - 1) * 50)
            .limit(50)
        ).all()
    )


def cabinet_context(
    request: Request,
    session: Session,
    customer: Customer | None,
    *,
    auth_message: str = "",
    auth_error: str = "",
    pending_phone: str = "",
    awaiting_phone_code: bool = False,
    phone_code_hint: str = "",
    page: int = 1,
) -> dict[str, Any]:
    appointment_rows = []
    total = (
        count_rows(session, Appointment, customer_appointment_filter(customer)) if customer else 0
    )
    pages = max(1, (total + 49) // 50)
    page = min(page, pages)
    if customer is not None:
        appointment_rows = build_appointment_rows(customer_appointments(customer, session, page))

    return {
        "request": request,
        "app_name": settings.app_name,
        "asset_version": ASSET_VERSION,
        "customer": customer,
        "is_admin": is_google_admin(request, customer),
        "appointment_rows": appointment_rows,
        "format_date": format_date,
        "format_time": format_time,
        "google_enabled": google_auth_enabled(),
        "auth_message": auth_message,
        "auth_error": auth_error,
        "pending_phone": pending_phone,
        "awaiting_phone_code": awaiting_phone_code,
        "phone_code_hint": phone_code_hint,
        "show_debug_phone_code": settings.phone_auth_debug and settings.environment != "production",
        "phone_enabled": settings.phone_auth_debug and settings.environment != "production",
        "pagination_path": "/cabinet",
        "page": page,
        "pages": pages,
    }


def google_redirect_uri(request: Request) -> str:
    if settings.google_redirect_uri:
        return settings.google_redirect_uri
    if settings.public_base_url:
        return f"{settings.public_base_url}/auth/google/callback"
    return str(request.url_for("google_callback"))


def exchange_google_code(request: Request, code: str) -> dict[str, Any]:
    token_payload = {
        "code": code,
        "client_id": settings.google_client_id or "",
        "client_secret": (
            settings.google_client_secret.get_secret_value()
            if settings.google_client_secret
            else ""
        ),
        "redirect_uri": google_redirect_uri(request),
        "grant_type": "authorization_code",
        "code_verifier": request.cookies.get(cookie_name(OAUTH_VERIFIER_COOKIE_NAME), ""),
    }
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.post("https://oauth2.googleapis.com/token", data=token_payload)
            response.raise_for_status()
            token_data = response.json()
            access_token = token_data.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise ValueError("Missing access token")
            response = client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            profile = response.json()
            if not isinstance(profile, dict):
                raise TypeError("Invalid profile")
            return profile
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не вдалося увійти через Google. Спробуйте ще раз.",
        ) from exc


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
            "booking_months": build_booking_calendar(session),
            "weekday_labels": WEEKDAY_LABELS,
        },
    )


@app.get("/cabinet", response_class=HTMLResponse)
def cabinet(
    request: Request,
    session: SessionDep,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
) -> HTMLResponse:
    auth_error = ""
    if request.query_params.get("auth_error") == "google_not_configured":
        auth_error = "Google-вхід тимчасово недоступний. Спробуйте пізніше."
    elif request.query_params.get("auth_error") == "google_failed":
        auth_error = "Не вдалося увійти через Google. Спробуйте ще раз."

    customer = get_customer_by_session(request, session)
    return templates.TemplateResponse(
        request,
        "cabinet.html",
        cabinet_context(request, session, customer, auth_error=auth_error, page=page),
    )


@app.post("/auth/phone/request", response_class=HTMLResponse)
def request_phone_code(
    request: Request,
    session: SessionDep,
    form_data: FormDataDep,
) -> HTMLResponse:
    if not settings.phone_auth_debug or settings.environment == "production":
        raise HTTPException(503, "Вхід телефоном тимчасово недоступний.")
    rate_limit(session, request, "phone-request-ip", 10, 600)
    raw_phone = form_data.get("phone", "")
    phone = normalize_phone(raw_phone)
    if phone is None:
        return templates.TemplateResponse(
            request,
            "cabinet.html",
            cabinet_context(
                request,
                session,
                None,
                auth_error="Введіть коректний номер телефону.",
                pending_phone=raw_phone,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    rate_limit(session, request, "phone-request-number", 1, 60, identifier=phone)
    rate_limit(session, request, "phone-request-hour", 5, 3600, identifier=phone)
    now = utc_now()
    session.execute(
        update(PhoneAuthCode)
        .where(
            PhoneAuthCode.phone == phone,
            PhoneAuthCode.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    session.execute(delete(PhoneAuthCode).where(PhoneAuthCode.expires_at < now))
    code = f"{secrets.randbelow(1_000_000):06d}"
    session.add(
        PhoneAuthCode(
            phone=phone,
            code_hash=hash_phone_code(phone, code),
            expires_at=utc_now() + timedelta(minutes=PHONE_CODE_TTL_MINUTES),
        )
    )
    session.commit()
    return templates.TemplateResponse(
        request,
        "cabinet.html",
        cabinet_context(
            request,
            session,
            None,
            auth_message="Введіть код підтвердження.",
            pending_phone=phone,
            awaiting_phone_code=True,
            phone_code_hint=code,
        ),
    )


@app.post("/auth/phone/verify", response_model=None)
def verify_phone_code(
    request: Request,
    session: SessionDep,
    form_data: FormDataDep,
) -> RedirectResponse | HTMLResponse:
    if not settings.phone_auth_debug or settings.environment == "production":
        raise HTTPException(503, "Вхід телефоном тимчасово недоступний.")
    rate_limit(session, request, "phone-verify-ip", 30, 600)
    phone = normalize_phone(form_data.get("phone", ""))
    code = form_data.get("code", "").strip()
    if phone is None or not re.fullmatch(r"[0-9]{6}", code):
        return templates.TemplateResponse(
            request,
            "cabinet.html",
            cabinet_context(
                request,
                session,
                None,
                auth_error="Перевірте номер телефону і 6-значний код.",
                pending_phone=form_data.get("phone", ""),
                awaiting_phone_code=phone is not None,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    auth_code = session.exec(
        select(PhoneAuthCode)
        .where(PhoneAuthCode.phone == phone)
        .where(PhoneAuthCode.consumed_at.is_(None))
        .order_by(PhoneAuthCode.created_at.desc(), PhoneAuthCode.id.desc())
    ).first()
    if (
        auth_code is None
        or utc_expired(auth_code.expires_at)
        or auth_code.attempts >= PHONE_CODE_MAX_ATTEMPTS
    ):
        return templates.TemplateResponse(
            request,
            "cabinet.html",
            cabinet_context(
                request,
                session,
                None,
                auth_error="Запросіть новий код підтвердження.",
                pending_phone=phone,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    correct = secrets.compare_digest(auth_code.code_hash, hash_phone_code(phone, code))
    updated = session.execute(
        update(PhoneAuthCode)
        .where(
            PhoneAuthCode.id == auth_code.id,
            PhoneAuthCode.consumed_at.is_(None),
            PhoneAuthCode.attempts < PHONE_CODE_MAX_ATTEMPTS,
            PhoneAuthCode.expires_at > utc_now(),
        )
        .values(
            attempts=PhoneAuthCode.attempts + 1,
            consumed_at=utc_now() if correct else None,
        )
        .returning(PhoneAuthCode.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    session.commit()
    if not correct or updated is None:
        return templates.TemplateResponse(
            request,
            "cabinet.html",
            cabinet_context(
                request,
                session,
                None,
                auth_error="Не вдалося підтвердити код. Перевірте його або запросіть новий.",
                pending_phone=phone,
                awaiting_phone_code=True,
            ),
            status_code=400,
        )
    customer = upsert_phone_customer(session, phone)
    response = RedirectResponse(url="/cabinet", status_code=status.HTTP_303_SEE_OTHER)
    issue_customer_session(customer, session, response, request)
    return response


@app.get("/auth/google")
def google_login(request: Request) -> RedirectResponse:
    if not google_auth_enabled():
        return RedirectResponse(
            url="/cabinet?auth_error=google_not_configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    params = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": google_redirect_uri(request),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    response = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
    response.set_cookie(
        cookie_name(OAUTH_STATE_COOKIE_NAME),
        state,
        max_age=10 * 60,
        httponly=True,
        secure=secure_cookie_enabled(),
        samesite="lax",
    )
    response.set_cookie(
        cookie_name(OAUTH_VERIFIER_COOKIE_NAME),
        verifier,
        max_age=10 * 60,
        httponly=True,
        secure=secure_cookie_enabled(),
        samesite="lax",
    )
    return response


@app.get("/auth/google/callback", name="google_callback")
def google_callback(request: Request, session: SessionDep) -> RedirectResponse:
    state = request.query_params.get("state", "")
    expected_state = request.cookies.get(cookie_name(OAUTH_STATE_COOKIE_NAME), "")
    verifier = request.cookies.get(cookie_name(OAUTH_VERIFIER_COOKIE_NAME), "")
    code = request.query_params.get("code", "")
    response = RedirectResponse("/cabinet?auth_error=google_failed", status_code=303)
    if (
        google_auth_enabled()
        and state
        and code
        and 43 <= len(verifier) <= 128
        and secrets.compare_digest(state.encode(), expected_state.encode())
    ):
        try:
            profile = exchange_google_code(request, code)
            customer = upsert_google_customer(session, profile)
            response = RedirectResponse(url="/cabinet", status_code=status.HTTP_303_SEE_OTHER)
            issue_customer_session(customer, session, response, request, via_google=True)
        except (HTTPException, IntegrityError):
            session.rollback()
            response = RedirectResponse("/cabinet?auth_error=google_failed", status_code=303)
    for name in (OAUTH_STATE_COOKIE_NAME, OAUTH_VERIFIER_COOKIE_NAME):
        response.delete_cookie(
            cookie_name(name),
            secure=secure_cookie_enabled(),
            httponly=True,
            samesite="lax",
        )
    return response


@app.post("/auth/logout")
def logout(request: Request, session: SessionDep) -> RedirectResponse:
    response = RedirectResponse(url="/cabinet", status_code=status.HTTP_303_SEE_OTHER)
    clear_customer_session(request, session, response)
    return response


@admin_router.get("", response_class=HTMLResponse)
def admin(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin.html", admin_context(session, request))


@admin_router.get("/appointments", response_class=HTMLResponse)
def admin_appointments(
    request: Request,
    session: SessionDep,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    q: Annotated[str, Query(max_length=120)] = "",
    day: Annotated[str, Query(max_length=10)] = "",
    status_filter: Annotated[AppointmentStatus | Literal[""], Query(alias="status")] = "",
) -> HTMLResponse:
    visit_date = form_date({"day": day}, "day") if day else None
    return templates.TemplateResponse(
        request,
        "admin_appointments.html",
        appointments_context(session, request, page, q.strip(), visit_date, status_filter),
    )


@admin_router.post("/changes")
def save_admin_changes(changes: AdminChanges, session: SessionDep) -> dict[str, str]:
    try:
        apply_admin_changes(session, changes)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            409, "Конфлікт записів, назв або годин. Перевірте зміни й спробуйте ще раз."
        ) from exc
    return {"status": "ok"}


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


@app.get("/api/availability", response_model=list[AvailableTimeSlotRead])
def available_time_slots(preferred_date: date, session: SessionDep) -> list[AvailableTimeSlot]:
    today, window_end = booking_window()
    if not today <= preferred_date <= window_end:
        raise HTTPException(400, "Оберіть дату в межах доступного періоду.")
    if session.exec(select(ClosedDate).where(ClosedDate.closed_on == preferred_date)).first():
        return []
    ensure_seed_data(session)
    booked = select(Appointment.preferred_time).where(
        Appointment.preferred_date == preferred_date,
        Appointment.status != AppointmentStatus.canceled,
        Appointment.preferred_time.is_not(None),
    )
    statement = (
        select(AvailableTimeSlot)
        .where(
            AvailableTimeSlot.is_active,
            AvailableTimeSlot.start_time.not_in(booked),
        )
        .order_by(AvailableTimeSlot.sort_order, AvailableTimeSlot.id)
    )
    if preferred_date == today:
        statement = statement.where(AvailableTimeSlot.start_time > kyiv_now().time())
    return list(session.exec(statement).all())


@app.get("/api/closed-dates", response_model=list[ClosedDateRead])
def list_closed_dates(session: SessionDep) -> list[ClosedDate]:
    return list(
        session.exec(select(ClosedDate).order_by(ClosedDate.closed_on, ClosedDate.id)).all()
    )


@app.get("/api/products", response_model=list[ProductRead])
def list_products(session: SessionDep) -> list[Product]:
    return list(
        session.exec(
            select(Product).where(Product.is_active).order_by(Product.sort_order, Product.id)
        ).all()
    )


@app.post(
    "/api/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_product(payload: ProductCreate, session: SessionDep) -> Product:
    product = Product.model_validate(payload)
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


@app.post("/api/appointments", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
def create_appointment(
    request: Request, payload: AppointmentCreate, session: SessionDep
) -> Appointment:
    rate_limit(session, request, "appointment-create", 10, 3600)
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
        today, window_end = booking_window()
        if payload.preferred_date < today:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Оберіть майбутню дату.",
            )

        if payload.preferred_date > window_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Запис доступний не більше ніж на 30 днів вперед.",
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
    if payload.preferred_date == current_date() and payload.preferred_time <= kyiv_now().time():
        raise HTTPException(400, "Оберіть майбутній час прийому.")
    appointment.phone_normalized = normalize_phone(payload.phone)
    appointment.email_normalized = normalize_email(payload.email)
    customer = get_customer_by_session(request, session)
    if customer is not None:
        appointment.customer_id = customer.id
        if payload.name and not customer.name:
            customer.name = payload.name
        customer.updated_at = utc_now()
        session.add(customer)

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
    request: Request,
    payload: AnalyticsEventCreate,
    session: SessionDep,
) -> AnalyticsEvent:
    rate_limit(session, request, "analytics", 60, 60)
    event = AnalyticsEvent.model_validate(payload)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


@admin_router.post("/appointments/{appointment_id}/status")
def update_appointment_status(
    appointment_id: int,
    request: Request,
    session: SessionDep,
    form_data: FormDataDep,
) -> RedirectResponse:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Запис не знайдено.")

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


@admin_router.post("/services")
def create_service(session: SessionDep, form_data: FormDataDep) -> RedirectResponse:
    sort_order = session.exec(select(func.max(BookingService.sort_order))).one() or 0
    payload = validated_form(
        BookingServiceCreate,
        {
            "name": form_data.get("name", ""),
            "duration_minutes": form_int(form_data, "duration_minutes", 30),
            "price_uah": form_int(form_data, "price_uah", 0),
            "sort_order": form_int(form_data, "sort_order", sort_order + 1),
            "is_active": form_data.get("is_active") == "on",
        },
    )
    service = BookingService.model_validate(payload)

    session.add(service)
    session.commit()
    return RedirectResponse(url="/admin#services", status_code=status.HTTP_303_SEE_OTHER)


@admin_router.post("/reorder/services")
def reorder_services(payload: ReorderPayload, session: SessionDep) -> dict[str, str]:
    apply_sort_order(session, BookingService, payload.ids)
    return {"status": "ok"}


@admin_router.post("/services/{service_id}")
def update_service(
    service_id: int,
    session: SessionDep,
    form_data: FormDataDep,
) -> RedirectResponse:
    service = session.get(BookingService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Послугу не знайдено.")

    payload = validated_form(
        BookingServiceCreate,
        {
            "name": form_data.get("name", service.name),
            "duration_minutes": form_int(form_data, "duration_minutes", service.duration_minutes),
            "price_uah": form_int(form_data, "price_uah", service.price_uah),
            "sort_order": form_int(form_data, "sort_order", service.sort_order),
            "is_active": form_data.get("is_active") == "on",
        },
    )
    service.sqlmodel_update(payload.model_dump())
    service.updated_at = utc_now()
    session.add(service)
    session.commit()
    return RedirectResponse(url="/admin#services", status_code=status.HTTP_303_SEE_OTHER)


@admin_router.post("/time-slots")
def create_time_slot(session: SessionDep, form_data: FormDataDep) -> RedirectResponse:
    sort_order = session.exec(select(func.max(AvailableTimeSlot.sort_order))).one() or 0
    payload = validated_form(
        AvailableTimeSlotCreate,
        {
            "start_time": form_time(form_data, "start_time"),
            "end_time": form_time(form_data, "end_time"),
            "sort_order": form_int(form_data, "sort_order", sort_order + 1),
            "is_active": form_data.get("is_active") == "on",
        },
    )
    slot = AvailableTimeSlot.model_validate(payload)
    session.add(slot)
    session.commit()
    return RedirectResponse(url="/admin#time-slots", status_code=status.HTTP_303_SEE_OTHER)


@admin_router.post("/reorder/time-slots")
def reorder_time_slots(payload: ReorderPayload, session: SessionDep) -> dict[str, str]:
    apply_sort_order(session, AvailableTimeSlot, payload.ids)
    return {"status": "ok"}


@admin_router.post("/time-slots/{slot_id}")
def update_time_slot(
    slot_id: int,
    session: SessionDep,
    form_data: FormDataDep,
) -> RedirectResponse:
    slot = session.get(AvailableTimeSlot, slot_id)
    if slot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Годину не знайдено.")

    payload = validated_form(
        AvailableTimeSlotCreate,
        {
            "start_time": form_time(form_data, "start_time"),
            "end_time": form_time(form_data, "end_time"),
            "sort_order": form_int(form_data, "sort_order", slot.sort_order),
            "is_active": form_data.get("is_active") == "on",
        },
    )
    slot.sqlmodel_update(payload.model_dump())
    slot.updated_at = utc_now()
    session.add(slot)
    session.commit()
    return RedirectResponse(url="/admin#time-slots", status_code=status.HTTP_303_SEE_OTHER)


@admin_router.post("/closed-dates")
def create_closed_date(session: SessionDep, form_data: FormDataDep) -> RedirectResponse:
    payload = validated_form(
        ClosedDateCreate,
        {
            "closed_on": form_date(form_data, "closed_on"),
            "reason": form_data.get("reason", "").strip(),
        },
    )
    closed_date = ClosedDate.model_validate(payload)
    session.add(closed_date)
    session.commit()
    return RedirectResponse(url="/admin#closed-dates", status_code=status.HTTP_303_SEE_OTHER)


@admin_router.post("/closed-dates/{closed_date_id}")
def update_closed_date(
    closed_date_id: int,
    session: SessionDep,
    form_data: FormDataDep,
) -> RedirectResponse:
    closed_date = session.get(ClosedDate, closed_date_id)
    if closed_date is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Закриту дату не знайдено."
        )

    payload = validated_form(
        ClosedDateCreate,
        {
            "closed_on": form_date(form_data, "closed_on"),
            "reason": form_data.get("reason", "").strip(),
        },
    )
    closed_date.sqlmodel_update(payload.model_dump())
    closed_date.updated_at = utc_now()
    session.add(closed_date)
    session.commit()
    return RedirectResponse(url="/admin#closed-dates", status_code=status.HTTP_303_SEE_OTHER)


@admin_router.post("/closed-dates/{closed_date_id}/delete")
def delete_closed_date(closed_date_id: int, session: SessionDep) -> RedirectResponse:
    closed_date = session.get(ClosedDate, closed_date_id)
    if closed_date is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Закриту дату не знайдено."
        )

    session.delete(closed_date)
    session.commit()
    return RedirectResponse(url="/admin#closed-dates", status_code=status.HTTP_303_SEE_OTHER)


app.include_router(admin_router)
