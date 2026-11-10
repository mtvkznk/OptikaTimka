from datetime import UTC, date, datetime, time
from enum import Enum

from pydantic import EmailStr, field_validator, model_validator
from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.validation import normalize_email, normalize_phone


def utc_now() -> datetime:
    return datetime.now(UTC)


class AppointmentStatus(str, Enum):
    new = "new"
    confirmed = "confirmed"
    canceled = "canceled"


class ContentBlockBase(SQLModel):
    slug: str = Field(max_length=80, index=True)
    nav_label: str = Field(max_length=80)
    title: str = Field(max_length=180)
    body: str = Field(default="", max_length=2000)
    sort_order: int = Field(default=0, index=True)
    is_visible: bool = Field(default=True, index=True)


class ContentBlock(ContentBlockBase, table=True):
    __table_args__ = (UniqueConstraint("slug"),)

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ContentBlockCreate(ContentBlockBase):
    pass


class ContentBlockRead(ContentBlockBase):
    id: int
    created_at: datetime
    updated_at: datetime


class ProductBase(SQLModel):
    category: str = Field(max_length=80, index=True)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    price_cents: int = Field(default=0, ge=0)
    currency: str = Field(default="UAH", max_length=3)
    image_url: str | None = Field(default=None, max_length=500)
    is_active: bool = Field(default=True, index=True)
    sort_order: int = Field(default=0, index=True)


class Product(ProductBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProductCreate(ProductBase):
    pass


class ProductRead(ProductBase):
    id: int
    created_at: datetime
    updated_at: datetime


class BookingServiceBase(SQLModel):
    name: str = Field(max_length=160)
    duration_minutes: int = Field(default=30, ge=5, le=480)
    price_uah: int = Field(default=0, ge=0, le=1_000_000)
    is_active: bool = Field(default=True, index=True)
    sort_order: int = Field(default=0, index=True)


class BookingService(BookingServiceBase, table=True):
    __table_args__ = (UniqueConstraint("name"),)

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BookingServiceCreate(BookingServiceBase):
    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Service name is required")
        return value.strip()


class BookingServiceRead(BookingServiceBase):
    id: int
    created_at: datetime
    updated_at: datetime


class AvailableTimeSlotBase(SQLModel):
    start_time: time
    end_time: time
    is_active: bool = Field(default=True, index=True)
    sort_order: int = Field(default=0, index=True)


class AvailableTimeSlot(AvailableTimeSlotBase, table=True):
    __table_args__ = (UniqueConstraint("start_time"),)

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AvailableTimeSlotCreate(AvailableTimeSlotBase):
    @model_validator(mode="after")
    def validate_interval(self) -> "AvailableTimeSlotCreate":
        if (
            self.start_time.tzinfo
            or self.end_time.tzinfo
            or self.start_time >= self.end_time
            or self.start_time.second
            or self.end_time.second
            or self.start_time.microsecond
            or self.end_time.microsecond
        ):
            raise ValueError("The end time must follow the start time, in whole local minutes")
        return self


class AvailableTimeSlotRead(AvailableTimeSlotBase):
    id: int
    created_at: datetime
    updated_at: datetime


class ClosedDateBase(SQLModel):
    closed_on: date = Field(index=True)
    reason: str = Field(default="", max_length=160)


class ClosedDate(ClosedDateBase, table=True):
    __table_args__ = (UniqueConstraint("closed_on"),)

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ClosedDateCreate(ClosedDateBase):
    pass


class ClosedDateRead(ClosedDateBase):
    id: int
    created_at: datetime
    updated_at: datetime


class AppointmentBase(SQLModel):
    name: str = Field(max_length=120)
    phone: str = Field(max_length=40)
    email: str | None = Field(default=None, max_length=255)
    service: str = Field(max_length=160)
    preferred_date: date | None = None
    preferred_time: time | None = None
    message: str = Field(default="", max_length=1000)


class Appointment(AppointmentBase, table=True):
    __table_args__ = (
        Index(
            "uq_appointment_active_slot",
            "preferred_date",
            "preferred_time",
            unique=True,
            sqlite_where=text("status != 'canceled'"),
            postgresql_where=text("status != 'canceled'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    customer_id: int | None = Field(default=None, foreign_key="customer.id", index=True)
    phone_normalized: str | None = Field(default=None, max_length=40, index=True)
    email_normalized: str | None = Field(default=None, max_length=255, index=True)
    status: AppointmentStatus = Field(default=AppointmentStatus.new, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class AppointmentCreate(AppointmentBase):
    preferred_date: date
    preferred_time: time
    email: EmailStr | None = None

    @field_validator("name", "service")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("This field must not be blank")
        return value.strip()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        phone = normalize_phone(value)
        if not phone:
            raise ValueError("Enter an international phone number with 8 to 15 digits")
        return phone

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return normalize_email(value)

    @field_validator("preferred_time")
    @classmethod
    def validate_time(cls, value: time) -> time:
        if value.tzinfo or value.second or value.microsecond:
            raise ValueError("Select a local time slot in whole minutes")
        return value


class AppointmentRead(AppointmentBase):
    id: int
    status: AppointmentStatus
    created_at: datetime
    updated_at: datetime


class Customer(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("phone"),
        UniqueConstraint("email"),
        UniqueConstraint("google_sub"),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(default="", max_length=160)
    phone: str | None = Field(default=None, max_length=40, index=True)
    email: str | None = Field(default=None, max_length=255, index=True)
    google_sub: str | None = Field(default=None, max_length=255, index=True)
    phone_verified: bool = False
    email_verified: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_login_at: datetime | None = Field(default=None)


class PhoneAuthCode(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    phone: str = Field(max_length=40, index=True)
    code_hash: str = Field(max_length=128, index=True)
    attempts: int = Field(default=0)
    expires_at: datetime = Field(index=True)
    consumed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class CustomerSession(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("token_hash"),)

    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    token_hash: str = Field(max_length=128, index=True)
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class AnalyticsEventBase(SQLModel):
    event_type: str = Field(max_length=40, index=True)
    path: str = Field(max_length=200)


class AnalyticsEvent(AnalyticsEventBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class AnalyticsEventCreate(AnalyticsEventBase):
    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        if value not in {"page_view", "booking_started", "booking_completed"}:
            raise ValueError("Unknown analytics event")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "?" in value or "#" in value:
            raise ValueError("Use a local path without query parameters or fragments")
        return value


class AnalyticsEventRead(AnalyticsEventBase):
    id: int
    created_at: datetime


class SecurityRateLimit(SQLModel, table=True):
    key: str = Field(primary_key=True, max_length=64)
    hits: int = 1
    expires_at: datetime = Field(index=True)
