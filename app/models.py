from datetime import UTC, date, datetime, time
from enum import Enum

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


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
    name: str = Field(max_length=160)
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


class AppointmentBase(SQLModel):
    name: str = Field(max_length=120)
    phone: str = Field(max_length=40)
    email: str | None = Field(default=None, max_length=255)
    service: str = Field(max_length=160)
    preferred_date: date | None = None
    preferred_time: time | None = None
    message: str = Field(default="", max_length=1000)


class Appointment(AppointmentBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    status: AppointmentStatus = Field(default=AppointmentStatus.new, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class AppointmentCreate(AppointmentBase):
    pass


class AppointmentRead(AppointmentBase):
    id: int
    status: AppointmentStatus
    created_at: datetime
    updated_at: datetime


class AnalyticsEventBase(SQLModel):
    event_type: str = Field(max_length=40, index=True)
    path: str = Field(max_length=200)


class AnalyticsEvent(AnalyticsEventBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class AnalyticsEventCreate(AnalyticsEventBase):
    pass


class AnalyticsEventRead(AnalyticsEventBase):
    id: int
    created_at: datetime

