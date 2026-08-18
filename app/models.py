from datetime import UTC, datetime

from sqlmodel import Field, Relationship, SQLModel


class ServiceBase(SQLModel):
    title: str = Field(max_length=120)
    description: str = Field(max_length=500)
    price_uah: int = Field(ge=0)
    sort_order: int = Field(default=0, index=True)


class Service(ServiceBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    appointments: list["Appointment"] = Relationship(back_populates="service")


class ServiceRead(ServiceBase):
    id: int


class AppointmentBase(SQLModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=40)
    service_id: int | None = Field(default=None, foreign_key="service.id")
    message: str | None = Field(default=None, max_length=800)


class Appointment(AppointmentBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    service: Service | None = Relationship(back_populates="appointments")


class AppointmentCreate(AppointmentBase):
    pass


class AppointmentRead(AppointmentBase):
    id: int
    created_at: datetime
