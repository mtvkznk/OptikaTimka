import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Any, Self

from fastapi import HTTPException
from pydantic import BaseModel, Field, PositiveInt, StringConstraints, model_validator
from sqlalchemy import func
from sqlmodel import Session, select

from app.admin_audit import add_admin_audit
from app.booking import (
    Interval,
    check_reactivation,
    lock_booking_writes,
    minute_of,
    working_intervals,
)
from app.catalog import (
    DEFAULT_CATEGORIES,
    active_products,
    category_names,
    category_records,
    product_category,
)
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
    ServiceClosedDate,
    ServiceSchedule,
    ServiceWeekdaySchedule,
    utc_now,
)
from app.product_photos import (
    MAX_PHOTO_PAYLOAD,
    prepare_photo,
    product_photo_url,
    update_product_photo,
)
from app.schedule_review import appointments_outside_schedule

MAX_PERIOD_DAYS = 366
CategorySlug = Annotated[
    str, StringConstraints(max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]
CategoryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


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
    name: CategoryName


class CategoryCreate(BaseModel):
    id: Annotated[str, StringConstraints(pattern=r"^category-[0-9a-f]{32}$", max_length=41)]
    name: CategoryName


class ProductChange(BaseModel):
    id: PositiveInt | None = None
    version: str | None = Field(default=None, max_length=48)
    category: CategorySlug
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    price: Decimal = Field(ge=0, le=Decimal("21474836.47"), decimal_places=2)
    photo: str = Field(default="", max_length=MAX_PHOTO_PAYLOAD)


class CatalogOrder[T](BaseModel):
    previous: list[T] = Field(max_length=5000)
    positions: list[T] = Field(max_length=5000)

    def check(self, current: list[T]) -> None:
        if self.previous != current or len(self.positions) != len(current) or (
            set(self.positions) != set(current)
        ):
            raise HTTPException(409, "Порядок у каталозі змінився. Скиньте чернетку й перевірте список.")


class ScheduleHours(BaseModel):
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


class WorkScheduleChange(ScheduleHours):
    version: str = Field(min_length=64, max_length=64)


def schedule_version(intervals: list[Interval]) -> str:
    return hashlib.sha256(repr(intervals).encode("ascii")).hexdigest()


def schedule_for_admin(intervals: list[Interval]) -> dict[str, Any]:
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


class WeekdayScheduleChange(BaseModel):
    version: str = Field(min_length=64, max_length=64)
    schedule: ScheduleHours | None


class ServiceAvailabilityChange(BaseModel):
    id: PositiveInt
    schedule_version: str | None = Field(default=None, min_length=64, max_length=64)
    schedule: ScheduleHours | None = None
    weekday_schedules: dict[Annotated[int, Field(ge=0, le=6)], WeekdayScheduleChange] = Field(
        default_factory=dict, max_length=7,
    )
    closed_days: ClosedDayChanges = Field(default_factory=ClosedDayChanges)


class AdminChanges(BaseModel):
    schedule_acknowledgement: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    appointments: list[AppointmentChange] = Field(default_factory=list, max_length=1000)
    services: list[ServiceChange] = Field(default_factory=list, max_length=500)
    categories: list[CategoryChange] = Field(default_factory=list, max_length=500)
    create_categories: list[CategoryCreate] = Field(default_factory=list, max_length=500)
    remove_categories: list[CategoryVersion] = Field(default_factory=list, max_length=500)
    restore_categories: list[CategoryVersion] = Field(default_factory=list, max_length=500)
    purge_categories: list[CategoryVersion] = Field(default_factory=list, max_length=500)
    category_order: CatalogOrder[CategorySlug] | None = None
    products: list[ProductChange] = Field(default_factory=list, max_length=500)
    remove_products: list[RowVersion] = Field(default_factory=list, max_length=500)
    product_order: CatalogOrder[PositiveInt] | None = None
    time_slots: list[SlotChange] = Field(default_factory=list, max_length=500)
    work_schedule: WorkScheduleChange | None = None
    service_availability: list[ServiceAvailabilityChange] = Field(default_factory=list, max_length=500)
    closed_periods: list[ClosedPeriodChange] = Field(default_factory=list, max_length=25)
    closed_days: ClosedDayChanges = Field(default_factory=ClosedDayChanges)
    remove_services: list[RowVersion] = Field(default_factory=list, max_length=500)
    remove_time_slots: list[RowVersion] = Field(default_factory=list, max_length=500)
    service_order: list[PositiveInt] | None = Field(default=None, max_length=1000)
    time_slot_order: list[PositiveInt] | None = Field(default=None, max_length=1000)


def verify_version(row: Any, version: str | None) -> None:
    if row is not None and version:
        try:
            submitted = datetime.fromisoformat(version)
        except ValueError:
            pass
        else:
            actual = row.updated_at
            if submitted.replace(tzinfo=submitted.tzinfo or UTC) == actual.replace(
                tzinfo=actual.tzinfo or UTC,
            ):
                return
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
    restorations: list[CategoryVersion], additions: list[CategoryCreate],
) -> None:
    edits = [*changes, *removals]
    all_changes = [*edits, *restorations, *additions]
    if len({change.id for change in all_changes}) != len(all_changes):
        raise HTTPException(422, "Одну категорію змінено кілька разів.")
    for change in edits:
        removing = not isinstance(change, CategoryChange)
        category = session.get(CatalogCategory, change.id)
        if category is None and not change.version and change.id in DEFAULT_CATEGORIES:
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
    for change in restorations:
        category = session.get(CatalogCategory, change.id)
        verify_version(category, change.version)
        if not category.is_removed or category.is_purged:
            raise HTTPException(409, "Категорія вже доступна. Оновіть сторінку.")
        category.is_removed = False
        category.sort_order = None
        category.updated_at = utc_now()
    if additions:
        names = {" ".join(name.casefold().split()) for name in category_names(session).values()}
        for addition in additions:
            name = " ".join(addition.name.casefold().split())
            if name in names:
                raise HTTPException(409, "Категорія з такою назвою вже є в каталозі.")
            if session.get(CatalogCategory, addition.id) is not None:
                raise HTTPException(409, "Категорію вже створено. Оновіть сторінку.")
            names.add(name)
            session.add(CatalogCategory(slug=addition.id, name=addition.name))


def apply_product_changes(
    session: Session, changes: list[ProductChange], removals: list[RowVersion],
    photos: dict[str, bytes],
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
        row = rows[removal.id]
        row.is_active = False
        row.updated_at = utc_now()
        if row.image_url == product_photo_url(row.id):
            update_product_photo(session, row, None)
    next_position = 0
    if any(change.id is None for change in changes):
        next_position = session.exec(
            select(func.max(Product.sort_order)).where(Product.is_active)
        ).one() or 0
    for change in changes:
        values = change.model_dump(include={"category", "name", "description"})
        values["price_cents"] = int(change.price * 100)
        if change.id is None:
            next_position += 1
            values["sort_order"] = next_position
            row = Product.model_validate(values)
            session.add(row)
        else:
            row = rows[change.id]
            row.sqlmodel_update(values)
            row.updated_at = utc_now()
        if change.photo:
            update_product_photo(session, row, photos.get(change.photo))


def purge_categories(session: Session, removals: list[CategoryVersion]) -> None:
    if len({change.id for change in removals}) != len(removals):
        raise HTTPException(422, "Одну категорію змінено кілька разів.")
    for change in removals:
        category = session.get(CatalogCategory, change.id)
        verify_version(category, change.version)
        if not category.is_removed or category.is_purged:
            raise HTTPException(409, "Категорія вже недоступна для видалення. Оновіть сторінку.")
        if session.exec(select(Product.id).where(
            Product.is_active, product_category() == change.id,
        ).limit(1)).first() is not None:
            raise HTTPException(
                409, f"Спочатку перенесіть або видаліть товари категорії «{category.name}».",
            )
        # Retain only the identifier so the old address stays retired, including built-in categories.
        category.is_purged = True
        category.name = ""
        category.sort_order = None
        category.updated_at = utc_now()


def save_category_order(session: Session, order: CatalogOrder[CategorySlug]) -> None:
    records = category_records(session)
    ranked = dict.fromkeys([*order.positions, *records])
    position = 0
    for slug in ranked:
        row = records[slug]
        if row is not None and row.is_removed:
            continue
        position += 1
        if row is None:
            session.add(CatalogCategory(slug=slug, name=DEFAULT_CATEGORIES[slug], sort_order=position))
        else:
            row.sort_order = position


def save_product_order(session: Session, order: CatalogOrder[PositiveInt]) -> None:
    products = {row.id: row for row in session.exec(active_products())}
    ranked = dict.fromkeys([*order.positions, *products])
    for position, product_id in enumerate((key for key in ranked if key in products), start=1):
        products[product_id].sort_order = position


def apply_closed_date_changes(
    session: Session, periods: list[ClosedPeriodChange], days: ClosedDayChanges,
    service_id: int | None = None,
) -> None:
    if not periods and not days.close and not days.reopen:
        return
    statement = (
        select(ClosedDate).order_by(ClosedDate.id) if service_id is None else
        select(ServiceClosedDate).where(ServiceClosedDate.service_id == service_id)
        .order_by(ServiceClosedDate.id)
    )
    rows = {row.id: row for row in session.exec(statement.with_for_update())}
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
    for day, reason in final.items():
        session.add(
            ClosedDate(closed_on=day, reason=reason) if service_id is None else
            ServiceClosedDate(service_id=service_id, closed_on=day, reason=reason)
        )


ServiceHours = ServiceSchedule | ServiceWeekdaySchedule


def service_schedule_version(schedule: ServiceHours | None) -> str:
    intervals = None if schedule is None else [tuple(interval) for interval in schedule.intervals]
    return hashlib.sha256(repr(intervals).encode("ascii")).hexdigest()


def save_service_schedule(
    session: Session, model: type[ServiceSchedule] | type[ServiceWeekdaySchedule], key: dict[str, int],
    version: str, schedule: ScheduleHours | None,
) -> None:
    current = session.get(model, key)
    if service_schedule_version(current) != version:
        raise HTTPException(409, "Графік послуги вже змінили. Оновіть сторінку.")
    if schedule is None:
        if current is not None:
            session.delete(current)
    elif current is None:
        session.add(model(**key, intervals=schedule.intervals()))
    else:
        current.intervals = schedule.intervals()


def apply_service_availability(session: Session, changes: list[ServiceAvailabilityChange]) -> None:
    if len({change.id for change in changes}) != len(changes):
        raise HTTPException(422, "Графік однієї послуги змінено кілька разів.")
    for change in changes:
        service = session.get(BookingService, change.id)
        if service is None or not service.is_active:
            raise HTTPException(409, "Послугу видалено. Оновіть сторінку.")
        if change.schedule is not None and change.schedule_version is None:
            raise HTTPException(422, "Оновіть сторінку перед зміною графіка послуги.")
        if change.schedule_version is not None:
            save_service_schedule(
                session, ServiceSchedule, {"service_id": change.id}, change.schedule_version, change.schedule,
            )
        for weekday, edit in change.weekday_schedules.items():
            save_service_schedule(
                session, ServiceWeekdaySchedule, {"service_id": change.id, "weekday": weekday},
                edit.version, edit.schedule,
            )
        apply_closed_date_changes(session, [], change.closed_days, change.id)


def closure_summary(days: ClosedDayChanges) -> str:
    parts = []
    if days.close:
        dates = ", ".join(day.strftime("%d.%m.%Y") for day in days.close[:5])
        if len(days.close) > 5:
            dates += f" і ще {len(days.close) - 5}"
        parts.append(f"закрито: {dates}")
    if days.reopen:
        parts.append(f"відкрито дат: {len(days.reopen)}")
    return ", ".join(parts)


def hours_summary(schedule: ScheduleHours | None) -> str:
    if schedule is None:
        return "успадковані години"
    if schedule.workday is None:
        return "без робочих годин"
    opens = schedule.workday.start_time.strftime("%H:%M")
    closes = schedule.workday.end_time.strftime("%H:%M")
    breaks = ", ".join(
        f"{pause.start_time:%H:%M}–{pause.end_time:%H:%M}" for pause in schedule.breaks
    )
    return f"{opens}–{closes}" + (f", перерви: {breaks}" if breaks else "")


def schedule_edit_details(session: Session, changes: AdminChanges) -> list[str]:
    details = []
    if changes.work_schedule is not None:
        details.append("Загальний графік · " + hours_summary(changes.work_schedule))
    elif changes.time_slots or changes.remove_time_slots:
        details.append("Загальний графік · робочі години")
    general_closures = closure_summary(changes.closed_days)
    if general_closures or changes.closed_periods:
        periods = f"періодів: {len(changes.closed_periods)}" if changes.closed_periods else ""
        details.append("Загальний графік · " + ", ".join(filter(None, [general_closures, periods])))
    for edit in changes.services:
        service = session.get(BookingService, edit.id) if edit.id is not None else None
        if service is not None and (service.weekdays_mask, service.is_active) != (
            edit.weekdays_mask, edit.is_active,
        ):
            details.append(f"{service.name} · доступні дні або стан послуги")
    for removal in changes.remove_services:
        service = session.get(BookingService, removal.id)
        if service is not None and service.is_active:
            details.append(f"{service.name} · послугу вимкнено")
    for edit in changes.service_availability:
        service = session.get(BookingService, edit.id)
        name = service.name if service is not None else f"Послуга №{edit.id}"
        parts = []
        if edit.schedule_version is not None:
            parts.append("типові години: " + hours_summary(edit.schedule))
        if edit.weekday_schedules:
            weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")
            parts.extend(
                f"{weekdays[day]}: {hours_summary(change.schedule)}"
                for day, change in sorted(edit.weekday_schedules.items())
            )
        if closures := closure_summary(edit.closed_days):
            parts.append(closures)
        if parts:
            details.append(f"{name} · {', '.join(parts)}")
    return details


def apply_admin_changes(session: Session, changes: AdminChanges, *, actor: str, now: datetime) -> None:
    photos = {
        payload: prepare_photo(payload)
        for payload in {change.photo for change in changes.products} - {"", "remove"}
    }
    lock_booking_writes(session)
    schedule_edits = schedule_edit_details(session, changes)
    outside_before = (
        appointments_outside_schedule(session, now, include_canceled=True) if schedule_edits else {}
    )
    if changes.category_order is not None:
        changes.category_order.check(list(category_names(session)))
    if changes.product_order is not None:
        changes.product_order.check([row.id for row in session.exec(active_products())])
    if {change.id for change in changes.purge_categories} & {
        change.id for change in [*changes.remove_categories, *changes.restore_categories]
    }:
        raise HTTPException(422, "Одну категорію змінено кілька разів.")
    if changes.work_schedule is not None and (
        changes.time_slots or changes.remove_time_slots or changes.time_slot_order is not None
    ):
        raise HTTPException(422, "Збережіть робочий графік окремо від старого списку годин.")
    apply_appointment_changes(session, changes.appointments)
    apply_category_changes(
        session, changes.categories, changes.remove_categories, changes.restore_categories,
        changes.create_categories,
    )
    apply_product_changes(session, changes.products, changes.remove_products, photos)
    purge_categories(session, changes.purge_categories)
    if changes.category_order is not None:
        save_category_order(session, changes.category_order)
    if changes.product_order is not None:
        save_product_order(session, changes.product_order)
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
    apply_service_availability(session, changes.service_availability)
    if schedule_edits:
        session.flush()
        affected = {
            visit_id: info for visit_id, info in appointments_outside_schedule(session, now).items()
            if visit_id not in outside_before
        }
        if affected:
            draft = changes.model_dump_json(exclude={"schedule_acknowledgement"})
            token = hashlib.sha256(f"{draft}:{list(affected.items())}".encode()).hexdigest()
            if changes.schedule_acknowledgement != token:
                raise HTTPException(409, {
                    "kind": "schedule_review",
                    "token": token,
                    "total": len(affected),
                    "appointments": list(affected.values())[:8],
                })
        for detail in schedule_edits:
            add_admin_audit(session, actor, "schedule_changed", detail)
