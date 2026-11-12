import hashlib
from collections.abc import Sequence
from datetime import date, time, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Any, Self

from fastapi import HTTPException
from pydantic import BaseModel, Field, PositiveInt, StringConstraints, model_validator
from sqlmodel import Session, select

from app.booking import (
    Interval,
    check_reactivation,
    lock_booking_writes,
    minute_of,
    working_intervals,
)
from app.catalog import DEFAULT_CATEGORIES, CategorySlug, category_names
from app.models import (
    Appointment,
    AppointmentStatus,
    AvailableTimeSlot,
    AvailableTimeSlotCreate,
    BookingService,
    BookingServiceCreate,
    CatalogCategory,
    ClosedDate,
    Product,
    utc_now,
)

MAX_PERIOD_DAYS = 366


class RowVersion(BaseModel):
    id: PositiveInt
    version: str = Field(max_length=48)


class AppointmentChange(RowVersion):
    status: AppointmentStatus


class ServiceChange(BookingServiceCreate):
    id: PositiveInt | None = None
    version: str | None = Field(default=None, max_length=48)


class SlotChange(AvailableTimeSlotCreate):
    id: PositiveInt | None = None
    version: str | None = Field(default=None, max_length=48)


class CategoryVersion(BaseModel):
    id: CategorySlug
    version: str = Field(max_length=48)


class CategoryChange(CategoryVersion):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class ProductChange(BaseModel):
    id: PositiveInt | None = None
    version: str | None = Field(default=None, max_length=48)
    category: CategorySlug
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    price: Decimal = Field(ge=0, le=Decimal("21474836.47"), decimal_places=2)


class WorkScheduleChange(BaseModel):
    version: str = Field(min_length=64, max_length=64)
    workday: AvailableTimeSlotCreate | None = None
    breaks: list[AvailableTimeSlotCreate] = Field(default_factory=list, max_length=100)

    def intervals(self) -> list[Interval]:
        if self.workday is None:
            if self.breaks:
                raise HTTPException(422, "Спочатку вкажіть початок і кінець робочого дня.")
            return []

        opens = minute_of(self.workday.start_time)
        closes = minute_of(self.workday.end_time)
        cursor = opens
        intervals = []
        for pause in sorted(self.breaks, key=lambda item: item.start_time):
            start, end = minute_of(pause.start_time), minute_of(pause.end_time)
            if start <= opens or end >= closes or start < cursor:
                raise HTTPException(422, "Перерви мають бути всередині робочого дня й не перетинатися.")
            if cursor < start:
                intervals.append((cursor, start))
            cursor = end
        intervals.append((cursor, closes))
        return intervals


def schedule_version(intervals: list[Interval]) -> str:
    return hashlib.sha256(repr(intervals).encode("ascii")).hexdigest()


def schedule_for_admin(slots: list[AvailableTimeSlot]) -> dict[str, Any]:
    intervals = working_intervals(slots)

    def clock(minutes: int) -> str:
        return time(*divmod(minutes, 60)).isoformat(timespec="minutes")

    return {
        "version": schedule_version(intervals),
        "start_time": clock(intervals[0][0]) if intervals else "",
        "end_time": clock(intervals[-1][1]) if intervals else "",
        "breaks": [
            {"start_time": clock(left[1]), "end_time": clock(right[0])}
            for left, right in pairwise(intervals)
        ],
    }


def apply_work_schedule(session: Session, change: WorkScheduleChange) -> None:
    rows = {slot.start_time: slot for slot in session.exec(select(AvailableTimeSlot))}
    intervals = working_intervals([slot for slot in rows.values() if slot.is_active])
    if change.version != schedule_version(intervals):
        raise HTTPException(409, "Графік уже змінили. Скиньте чернетку й перевірте актуальні години.")

    desired = {
        time(*divmod(start, 60)): (time(*divmod(end, 60)), order)
        for order, (start, end) in enumerate(change.intervals(), start=1)
    }
    now = utc_now()
    for start, slot in rows.items():
        if start not in desired and slot.is_active:
            slot.is_active = False
            slot.updated_at = now
    for start, (end, order) in desired.items():
        slot = rows.get(start)
        if slot is None:
            session.add(AvailableTimeSlot(start_time=start, end_time=end, sort_order=order))
        elif not slot.is_active or slot.end_time != end or slot.sort_order != order:
            slot.is_active = True
            slot.end_time = end
            slot.sort_order = order
            slot.updated_at = now


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


class ClosedDayChanges(BaseModel):
    close: list[date] = Field(default_factory=list, max_length=1000)
    reopen: dict[PositiveInt, str] = Field(default_factory=dict, max_length=1000)
    reason: str = Field(default="", max_length=160)


class AdminChanges(BaseModel):
    appointments: list[AppointmentChange] = Field(default_factory=list, max_length=1000)
    services: list[ServiceChange] = Field(default_factory=list, max_length=500)
    categories: list[CategoryChange] = Field(default_factory=list, max_length=4)
    remove_categories: list[CategoryVersion] = Field(default_factory=list, max_length=4)
    products: list[ProductChange] = Field(default_factory=list, max_length=500)
    remove_products: list[RowVersion] = Field(default_factory=list, max_length=500)
    time_slots: list[SlotChange] = Field(default_factory=list, max_length=500)
    work_schedule: WorkScheduleChange | None = None
    closed_periods: list[ClosedPeriodChange] = Field(default_factory=list, max_length=25)
    closed_days: ClosedDayChanges = Field(default_factory=ClosedDayChanges)
    remove_services: list[RowVersion] = Field(default_factory=list, max_length=500)
    remove_time_slots: list[RowVersion] = Field(default_factory=list, max_length=500)
    service_order: list[PositiveInt] | None = Field(default=None, max_length=1000)
    time_slot_order: list[PositiveInt] | None = Field(default=None, max_length=1000)


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
            appointment = rows[change.id]
            if appointment.status == AppointmentStatus.canceled:
                check_reactivation(session, appointment)
            appointment.status = change.status
            appointment.updated_at = utc_now()


def apply_catalog_changes(
    session: Session,
    model: type[Any],
    changes: Sequence[ServiceChange | SlotChange],
    order: list[int] | None,
    removals: list[RowVersion],
) -> None:
    if not changes and not removals and order is None:
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
    for removal in removals:
        if removal.id in edited:
            raise HTTPException(422, "Один рядок змінено кілька разів.")
        verify_version(rows.get(removal.id), removal.version)
        edited.add(removal.id)
    if order is not None:
        visible_ids = {row.id for row in rows.values() if row.is_active}
        ordered_ids = set(order)
        if len(order) != len(ordered_ids) or (
            ordered_ids != visible_ids and ordered_ids != rows.keys()
        ):
            raise HTTPException(409, "Список змінився. Оновіть сторінку й перевірте порядок.")
        for index, item_id in enumerate(order, start=1):
            row = rows[item_id]
            if row.sort_order != index:
                row.sort_order = index
                row.updated_at = utc_now()

    for removal in removals:
        row = rows[removal.id]
        # Retain deleted rows so an empty catalog is not mistaken for a fresh installation.
        row.is_active = False
        row.updated_at = utc_now()

    key = "name" if model is BookingService else "start_time"
    changing_ids = {change.id for change in changes}
    retired = {
        getattr(row, key): row
        for row in rows.values()
        if not row.is_active and row.id not in changing_ids
    }
    # Free an old value if an existing row is renamed to a previously deleted value.
    reclaimed = {
        getattr(change, key) for change in changes if change.id is not None
    } & retired.keys()
    for value in reclaimed:
        session.delete(retired.pop(value))
    if reclaimed:
        session.flush()

    next_order = max((row.sort_order for row in rows.values() if row.is_active), default=0)
    for change in changes:
        values = change.model_dump(exclude={"id", "version", "sort_order"})
        if isinstance(change, ServiceChange) and "weekdays_mask" not in change.model_fields_set:
            values.pop("weekdays_mask")
        if change.id is None:
            next_order += 1
            row = retired.pop(values[key], None)
            values["sort_order"] = next_order
            if row is None:
                session.add(model.model_validate(values))
            else:
                row.sqlmodel_update(values)
                row.updated_at = utc_now()
        else:
            row = rows[change.id]
            row.sqlmodel_update(values)
            row.updated_at = utc_now()


def apply_category_changes(
    session: Session, changes: list[CategoryChange], removals: list[CategoryVersion],
) -> None:
    edits = [*changes, *removals]
    if len({change.id for change in edits}) != len(edits):
        raise HTTPException(422, "Одну категорію змінено кілька разів.")
    for change in edits:
        removing = not isinstance(change, CategoryChange)
        category = session.get(CatalogCategory, change.id)
        if category is None and not change.version:
            category = CatalogCategory(slug=change.id, name=DEFAULT_CATEGORIES[change.id])
            session.add(category)
        elif category is None or category.is_removed:
            raise HTTPException(409, "Категорію вже видалено. Оновіть сторінку.")
        else:
            verify_version(category, change.version)
        if removing:
            category.is_removed = True
        else:
            category.name = change.name
        category.updated_at = utc_now()


def apply_product_changes(
    session: Session, changes: list[ProductChange], removals: list[RowVersion],
) -> None:
    if changes:
        categories = category_names(session)
        if any(change.category not in categories for change in changes):
            raise HTTPException(409, "Оберіть для товару категорію, яка залишиться в каталозі.")
    edits = [change for change in [*changes, *removals] if change.id is not None]
    ids = {change.id for change in edits}
    if len(ids) != len(edits):
        raise HTTPException(422, "Один товар змінено кілька разів.")
    rows = {
        row.id: row for row in session.exec(
            select(Product).where(Product.id.in_(ids), Product.is_active)
            .order_by(Product.id).with_for_update()
        )
    } if ids else {}
    for edit in edits:
        verify_version(rows.get(edit.id), edit.version)

    for removal in removals:
        rows[removal.id].is_active = False
        rows[removal.id].updated_at = utc_now()
    for change in changes:
        values = change.model_dump(include={"category", "name", "description"})
        values["price_cents"] = int(change.price * 100)
        if change.id is None:
            session.add(Product.model_validate(values))
        else:
            row = rows[change.id]
            row.sqlmodel_update(values)
            row.updated_at = utc_now()


def apply_closed_date_changes(
    session: Session, periods: list[ClosedPeriodChange], days: ClosedDayChanges,
) -> None:
    if not periods and not days.close and not days.reopen:
        return
    rows = {
        row.id: row for row in session.exec(select(ClosedDate).order_by(ClosedDate.id).with_for_update())
    }
    removed: set[int] = set()
    for previous in [days.reopen, *(period.previous for period in periods)]:
        for item_id, version in previous.items():
            if item_id in removed:
                raise HTTPException(422, "Періоди для редагування перетинаються.")
            verify_version(rows.get(item_id), version)
            removed.add(item_id)

    final = {row.closed_on: row.reason for item_id, row in rows.items() if item_id not in removed}
    for day in days.close:
        final.setdefault(day, days.reason.strip())
    for change in periods:
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
    lock_booking_writes(session)
    if changes.work_schedule is not None and (
        changes.time_slots or changes.remove_time_slots or changes.time_slot_order is not None
    ):
        raise HTTPException(422, "Збережіть робочий графік окремо від старого списку годин.")
    apply_appointment_changes(session, changes.appointments)
    apply_category_changes(session, changes.categories, changes.remove_categories)
    apply_product_changes(session, changes.products, changes.remove_products)
    apply_catalog_changes(
        session, BookingService, changes.services, changes.service_order, changes.remove_services,
    )
    apply_catalog_changes(
        session, AvailableTimeSlot, changes.time_slots, changes.time_slot_order,
        changes.remove_time_slots,
    )
    if changes.work_schedule is not None:
        apply_work_schedule(session, changes.work_schedule)
    apply_closed_date_changes(session, changes.closed_periods, changes.closed_days)
