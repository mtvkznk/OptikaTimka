"""Private administrator bot backed by the website's appointment commands."""

import hashlib
import html
import json
import logging
import secrets
from datetime import UTC, date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, or_
from sqlmodel import Session, select

from app import diary_commands as commands
from app.booking import (
    get_active_time_slots,
    lock_booking_writes,
    weekday_schedules_by_service,
    weekly_working_intervals,
    working_intervals,
)
from app.config import settings
from app.database import get_session
from app.models import (
    Appointment,
    AppointmentStatus,
    ClosedDate,
    Customer,
    ServiceClosedDate,
    ServiceSchedule,
    TelegramAccount,
    TelegramAction,
    TelegramAuth,
    TelegramOutbox,
    TelegramState,
    TelegramUpdate,
    TelegramView,
    utc_now,
)
from app.security import TELEGRAM_WEBHOOK_PATH, hash_secret, is_google_admin, valid_telegram_webhook
from app.telegram_auth import unexpired
from app.telegram_transport import telegram_api

router = APIRouter()
logging.getLogger("httpx").setLevel(logging.WARNING)
STATUS = {"new": "🟠 Новий", "confirmed": "🟢 Підтверджено", "canceled": "⚪ Скасовано"}


def deliver_outbox(session: Session, *, telegram_id: int | None = None) -> bool:
    statement = select(TelegramOutbox).order_by(TelegramOutbox.id)
    if telegram_id is not None:
        statement = statement.where(
            or_(TelegramOutbox.telegram_id == telegram_id, TelegramOutbox.telegram_id.is_(None))
        )
    for row in session.exec(statement.limit(30)).all():
        view = session.get(TelegramView, row.telegram_id) if row.view_generation else None
        if row.view_generation and (view is None or view.generation != row.view_generation):
            session.delete(row)
            session.commit()
            continue
        allowed = True
        if row.admin_customer_id is not None:
            customer = session.get(Customer, row.admin_customer_id)
            account = session.get(TelegramAccount, row.admin_customer_id)
            allowed = (
                is_google_admin(customer)
                and account is not None
                and account.telegram_id == row.payload.get("chat_id")
            )
        if not allowed:
            if view:
                session.delete(view)
            session.delete(row)
            session.commit()
            continue
        if row.not_before and unexpired(row.not_before):
            return False
        if row.created_at.replace(tzinfo=row.created_at.tzinfo or UTC) < utc_now() - timedelta(
            minutes=10
        ):
            if view:
                if view.message_id is None:
                    session.delete(view)
                else:
                    view.fingerprint = ""
            session.delete(row)
            session.commit()
            continue
        result = telegram_api(row.method, row.payload)
        if result.missing_message and not row.is_refresh:
            result = telegram_api(
                "sendMessage", {k: v for k, v in row.payload.items() if k != "message_id"}
            )
        if not result:
            if result.terminal:
                if view:
                    session.delete(view)
                session.delete(row)
                session.commit()
                continue
            row.not_before = utc_now() + timedelta(seconds=result.retry_after)
            session.commit()
            return False
        if view and result.message_id is not None:
            view.message_id = result.message_id
        session.delete(row)
        session.commit()
    return True


class Bot:
    def __init__(
        self,
        session: Session,
        telegram_id: int,
        *,
        message_id: int | None = None,
        preview: bool = False,
    ):
        self.session, self.id, self.message_id = session, telegram_id, message_id
        self.customer: Customer | None = None
        self.preview = preview
        self.actions: dict[str, dict] = {}
        self.payload: dict = {}
        self.context: dict | None = None
        self.fingerprint = ""

    @property
    def actor(self) -> str:
        return f"Telegram · {self.customer.email or self.customer.id}"

    def button(self, text: str, op: str, **data: Any) -> dict:
        token = secrets.token_urlsafe(18)
        self.actions[token] = {"op": op, **data}
        self.session.add(
            TelegramAction(
                token=token,
                telegram_id=self.id,
                payload={"op": op, **data},
                expires_at=utc_now() + timedelta(minutes=20),
            )
        )
        return {"text": text, "callback_data": token}

    def show(
        self,
        text: str,
        rows: list[list[dict]] | None = None,
        *,
        edit: bool = True,
        view: dict | None = None,
    ) -> None:
        payload = {
            "chat_id": self.id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "reply_markup": {"inline_keyboard": rows or []},
        }
        if edit and self.message_id:
            payload["message_id"] = self.message_id
        semantic = {
            "text": text,
            "rows": [
                [
                    {
                        **button,
                        "callback_data": self.actions.get(
                            button.get("callback_data"), button.get("callback_data")
                        ),
                    }
                    for button in row
                ]
                for row in rows or []
            ],
        }
        self.fingerprint = hashlib.sha256(
            json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        self.payload, self.context = payload, view
        if not self.preview:
            self.publish()

    def publish(self, *, is_refresh: bool = False) -> None:
        generation = None
        # Only the latest screen is current; queued older prompts must not overwrite it.
        self.session.execute(
            delete(TelegramOutbox).where(
                TelegramOutbox.telegram_id == self.id,
                TelegramOutbox.method.in_(["sendMessage", "editMessageText"]),
            )
        )
        current = self.session.get(TelegramView, self.id)
        if self.context is not None and is_google_admin(self.customer):
            generation = secrets.token_urlsafe(18)
            values = {
                "customer_id": self.customer.id,
                "message_id": self.message_id,
                "context": self.context,
                "payload": self.payload,
                "fingerprint": self.fingerprint,
                "generation": generation,
            }
            if current is None:
                current = TelegramView(telegram_id=self.id, **values)
                self.session.add(current)
            else:
                for key, value in values.items():
                    setattr(current, key, value)
        elif current:
            self.session.delete(current)
        self.session.add(
            TelegramOutbox(
                method="editMessageText" if "message_id" in self.payload else "sendMessage",
                payload=self.payload,
                admin_customer_id=self.customer.id if is_google_admin(self.customer) else None,
                telegram_id=self.id,
                view_generation=generation,
                is_refresh=is_refresh,
            )
        )

    def admin(self) -> bool:
        account = self.session.exec(
            select(TelegramAccount).where(TelegramAccount.telegram_id == self.id)
        ).first()
        self.customer = self.session.get(Customer, account.customer_id) if account else None
        if is_google_admin(self.customer):
            return True
        self.show(
            "Щоб керувати записами, увійдіть на сайт Google-акаунтом адміністратора та прив’яжіть Telegram у кабінеті.",
            [[{"text": "Особистий кабінет", "url": f"{settings.public_base_url}/cabinet"}]],
        )
        return False

    def state(self, payload: dict | None = None) -> TelegramState | None:
        state = self.session.get(TelegramState, self.id)
        if payload is not None:
            if state is None:
                state = TelegramState(telegram_id=self.id, payload=payload, expires_at=utc_now())
            state.payload = payload
            state.expires_at = utc_now() + timedelta(minutes=10)
            self.session.add(state)
        return state

    def clear_state(self) -> None:
        state = self.session.get(TelegramState, self.id)
        if state:
            self.session.delete(state)

    def day(
        self,
        day: date | None = None,
        *,
        move: int | None = None,
        version: str = "",
        notice: str = "",
    ) -> None:
        from app.main import appointment_diary, booking_window, kyiv_now

        service = commands.require_diary_service(self.session)
        moving = self.session.get(Appointment, move) if move else None
        if move and (
            moving is None
            or moving.status == AppointmentStatus.canceled
            or not commands.appointment_belongs_to_service(moving, service)
            or moving.updated_at.isoformat() != version
        ):
            raise HTTPException(409, "Запис змінився. Виберіть його ще раз для перенесення.")
        week = weekly_working_intervals(
            working_intervals(get_active_time_slots(self.session)),
            self.session.get(ServiceSchedule, service.id),
            weekday_schedules_by_service(self.session, [service.id]).get(service.id, {}),
        )
        closed = set(self.session.exec(select(ClosedDate.closed_on)))
        closed.update(
            self.session.exec(
                select(ServiceClosedDate.closed_on).where(
                    ServiceClosedDate.service_id == service.id
                )
            )
        )
        now = kyiv_now()
        diary = appointment_diary(
            self.session,
            day or now.date(),
            service,
            week,
            closed,
            now=now,
            window_end=booking_window(now)[1],
            moving=moving,
        )
        if diary.get("date") is None:
            self.show(
                "Немає відкритих днів у графіку огляду лікаря.",
                view={"day": (day or now.date()).isoformat()},
            )
            return
        day = diary["date"]
        navdata = {"move": move, "version": version}
        rows = []
        nav = []
        if diary["previous"]:
            nav.append(self.button("← День", "day", day=diary["previous"].isoformat(), **navdata))
        nav.append(self.button("Дата", "date", day=day.isoformat(), **navdata))
        if diary["next"]:
            nav.append(self.button("День →", "day", day=diary["next"].isoformat(), **navdata))
        rows.append(nav)
        text = (
            f"<b>{html.escape(diary['weekday'])}, {day:%d.%m.%Y}</b>\n{html.escape(service.name)}"
        )
        if moving:
            text += f"\n\n<b>Перенесення прийому №{moving.id}</b>\nОберіть вільну годину нижче."
        if notice:
            text += "\n\n" + html.escape(notice)
        for row in diary["rows"]:
            starts = row["start"]
            data = {
                "day": day.isoformat(),
                "time": starts,
                "duration": service.duration_minutes,
                "service_id": service.id,
            }
            if row["visits"]:
                for visitrow in row["visits"]:
                    visit = visitrow["appointment"]
                    label = visit.name or visit.phone or "Без імені"
                    icon = "🟠" if visit.status == AppointmentStatus.new else "🟢"
                    rows.append(
                        [
                            self.button(
                                f"{starts} {icon} {label[:38]}",
                                "visit",
                                id=visit.id,
                                version=visit.updated_at.isoformat(),
                            )
                        ]
                    )
            elif row["other_occupied"] or row["doctor_occupied"]:
                text += f"\n{starts} — зайнято"
            elif row["can_reopen"]:
                rows.append(
                    [self.button(f"{starts} 🔴 Відкрити", "block", **data, block_action="open")]
                )
            elif row["can_move"]:
                rows.append(
                    [
                        self.button(
                            f"{starts} ✓ Підтвердити перенесення",
                            "move",
                            **data,
                            id=move,
                            version=version,
                        )
                    ]
                )
            elif row["can_book"]:
                rows.append(
                    [
                        self.button(f"{starts} ＋ Записати", "add", **data),
                        self.button("× Закрити", "block", **data, block_action="close"),
                    ]
                )
            else:
                text += f"\n{starts} — {'закрито' if row['is_blocked'] else 'час недоступний'}"
        rows.append(
            [
                self.button("Сьогодні", "day", **navdata),
                self.button("Усі записи", "list", page=0),
            ]
        )
        if move:
            rows.append([self.button("Скасувати перенесення", "day", day=day.isoformat())])
        self.show(
            text,
            rows,
            view={"day": day.isoformat(), "move": move, "version": version, "notice": notice},
        )

    def visit(self, visit_id: int, version: str | None = None) -> None:
        visit = self.session.get(Appointment, visit_id)
        if visit is None:
            raise HTTPException(404, "Запис видалений. Оновіть розклад.")
        if version and version != visit.updated_at.isoformat():
            raise HTTPException(409, "Запис змінився. Оновіть розклад.")
        data = {"id": visit.id, "version": visit.updated_at.isoformat()}
        text = (
            f"<b>Прийом №{visit.id}</b> · {commands.format_date(visit.preferred_date)} {commands.format_time(visit.preferred_time)}\n"
            f"{html.escape(visit.service)} · {visit.duration_minutes} хв\n\n"
            f"{html.escape((visit.name or 'Без імені')[:1200])}\n{html.escape((visit.phone or 'Без телефону')[:600])}\n"
            f"{STATUS[visit.status.value]}"
        )
        rows = [
            [
                self.button(label, "status", **data, status=value)
                for value, label in (("new", "🟠 Новий"), ("confirmed", "🟢 Підтвердити"))
            ],
            [self.button("Скасувати запис", "status", **data, status="canceled")],
        ]
        doctor = commands.diary_service(commands.get_active_services(self.session))
        if (
            doctor
            and commands.appointment_belongs_to_service(visit, doctor)
            and visit.status != AppointmentStatus.canceled
        ):
            rows.append(
                [
                    self.button(
                        "Перенести",
                        "day",
                        day=(visit.preferred_date or utc_now().date()).isoformat(),
                        move=visit.id,
                        version=data["version"],
                    )
                ]
            )
        rows.append([self.button("Видалити", "delete", **data)])
        rows.append(
            [
                self.button(
                    "← До розкладу",
                    "day",
                    day=(visit.preferred_date or utc_now().date()).isoformat(),
                )
            ]
        )
        self.show(text, rows)

    def listing(self, page: int = 0, q: str = "", status: str = "") -> None:
        statement = select(Appointment)
        if q:
            statement = statement.where(
                or_(
                    Appointment.name.icontains(q, autoescape=True),
                    Appointment.phone.icontains(q, autoescape=True),
                )
            )
        if status:
            statement = statement.where(Appointment.status == AppointmentStatus(status))
        visits = self.session.exec(
            statement.order_by(
                Appointment.preferred_date.desc(),
                Appointment.preferred_time.desc(),
                Appointment.id.desc(),
            )
            .offset(page * 8)
            .limit(9)
        ).all()
        rows = [
            [
                self.button(
                    f"{commands.format_date(v.preferred_date)} {commands.format_time(v.preferred_time)} · {(v.name or v.phone or 'Без імені')[:28]}",
                    "visit",
                    id=v.id,
                    version=v.updated_at.isoformat(),
                )
            ]
            for v in visits[:8]
        ]
        nav = []
        if page:
            nav.append(self.button("←", "list", page=page - 1, q=q, status=status))
        if len(visits) > 8:
            nav.append(self.button("→", "list", page=page + 1, q=q, status=status))
        if nav:
            rows.append(nav)
        rows.extend(
            [
                [self.button("Пошук", "search"), self.button("🟠 Нові", "list", status="new")],
                [self.button("Усі", "list"), self.button("Розклад", "day")],
            ]
        )
        self.show(
            f"<b>Усі записи · сторінка {page + 1}</b>"
            + (f"\nПошук: {html.escape(q)}" if q else "")
            + ("\nЗаписів немає." if not visits else ""),
            rows,
        )

    def authenticate(self, token: str, user: dict) -> None:
        pending = self.session.get(TelegramAuth, hash_secret(f"telegram-auth:{token}"))
        if pending is None or not unexpired(pending.expires_at) or pending.approved:
            self.show("Посилання більше не діє. Почніть вхід або прив’язку ще раз на сайті.")
            return
        if pending.telegram_id and pending.telegram_id != self.id:
            self.show("Посилання вже використане. Створіть нове на сайті.")
            return
        pending.telegram_id = self.id
        pending.username = str(user.get("username") or "")[:64]
        pending.display_name = " ".join(
            str(user.get(k) or "") for k in ("first_name", "last_name")
        ).strip()[:160]
        self.show(
            "Підтвердити "
            + (
                "прив’язку Telegram до відкритого кабінету"
                if pending.requester_id
                else "вхід на сайт"
            )
            + "?\n\nПідтверджуйте лише якщо ви щойно почали цю дію на optykatimka.com.ua.",
            [
                [
                    self.button("Підтвердити", "auth", auth_hash=pending.token_hash),
                    self.button("Відхилити", "auth_cancel", auth_hash=pending.token_hash),
                ]
            ],
        )

    def dispatch(self, data: dict) -> None:
        from app.main import kyiv_now

        op = data["op"]
        if op in {"auth", "auth_cancel"}:
            pending = self.session.get(TelegramAuth, data["auth_hash"])
            if (
                pending is None
                or not unexpired(pending.expires_at)
                or pending.telegram_id != self.id
                or pending.approved
            ):
                raise HTTPException(410, "Ця дія більше недоступна. Почніть ще раз на сайті.")
            if op == "auth_cancel":
                self.session.delete(pending)
                self.show("Дію відхилено.")
            else:
                pending.approved = True
                self.show(
                    "Підтверджено. Поверніться до тієї вкладки браузера, де починали дію, та завершіть вхід або прив’язку.",
                )
            return
        if not self.admin():
            return
        self.clear_state()
        now = kyiv_now()
        day = date.fromisoformat(data["day"]) if data.get("day") else now.date()
        common = {"now": now, "actor": self.actor, "locked": True}
        if op == "day":
            self.day(
                day,
                move=data.get("move"),
                version=data.get("version", ""),
            )
        elif op == "visit":
            self.visit(data["id"], data.get("version"))
        elif op == "list":
            self.listing(data.get("page", 0), data.get("q", ""), data.get("status", ""))
        elif op in {"search", "date"}:
            self.state({**data, "mode": op})
            self.show(
                "Введіть ім’я або телефон для пошуку."
                if op == "search"
                else "Введіть дату у форматі ДД.ММ.РРРР (наприклад, 30.11.2026).",
                [[self.button("Скасувати", "day", day=day.isoformat())]],
            )
        elif op == "add":
            flow = secrets.token_urlsafe(12)
            self.state({**data, "mode": "phone", "flow": flow})
            self.show(
                f"<b>{day:%d.%m.%Y} · {data['time']}</b>\nВведіть номер телефону або інший контакт.",
                [
                    [
                        self.button("Без телефону", "skip_phone", flow=flow),
                        self.button("Скасувати", "day", day=day.isoformat()),
                    ]
                ],
            )
        elif op == "block":
            commands.change_block(
                self.session,
                {
                    "preferred_date": data["day"],
                    "preferred_time": data["time"],
                    "service_id": str(data["service_id"]),
                    "slot_duration": str(data["duration"]),
                    "block_action": data["block_action"],
                },
                **common,
            )
            self.day(day)
        elif op == "move":
            commands.move_appointment(
                self.session,
                data["id"],
                {
                    "preferred_date": data["day"],
                    "preferred_time": data["time"],
                    "version": data["version"],
                },
                **common,
            )
            self.day(day)
        elif op == "status":
            commands.set_status(
                self.session,
                data["id"],
                {"status": data["status"], "version": data["version"]},
                **common,
            )
            self.visit(data["id"])
        elif op in {"delete", "delete_confirm"}:
            try:
                commands.remove_appointment(
                    self.session,
                    data["id"],
                    {
                        "version": data["version"],
                        "confirmed": "true" if op == "delete_confirm" else "false",
                    },
                    **common,
                )
            except HTTPException as exc:
                if isinstance(exc.detail, dict) and exc.detail.get("confirmation_required"):
                    self.show(
                        "Цей прийом ще попереду. Видалити запис остаточно?",
                        [
                            [
                                self.button(
                                    "Так, видалити",
                                    "delete_confirm",
                                    id=data["id"],
                                    version=data["version"],
                                ),
                                self.button("Залишити", "visit", id=data["id"]),
                            ]
                        ],
                    )
                    return
                raise
            self.listing()

    def manual_input(self, state: TelegramState, text: str, *, skip_name: bool = False) -> None:
        from app.main import kyiv_now

        data = state.payload.copy()
        if data["mode"] == "search":
            self.clear_state()
            self.listing(q=text.strip()[:160])
        elif data["mode"] == "date":
            try:
                dd, mm, yyyy = map(int, text.strip().split("."))
                day = date(yyyy, mm, dd)
            except ValueError:
                self.show("Не вдалося прочитати дату. Введіть ДД.ММ.РРРР.")
                return
            self.clear_state()
            self.day(day, move=data.get("move"), version=data.get("version", ""))
        elif data["mode"] == "phone":
            self.state({**data, "phone": text.strip(), "mode": "name"})
            self.show(
                "Введіть ім’я та прізвище або пропустіть цей крок.",
                [
                    [
                        self.button("Без імені", "skip_name", flow=data["flow"]),
                        self.button("Скасувати", "day", day=data["day"]),
                    ]
                ],
            )
        elif data["mode"] == "name":
            form = {
                "name": "" if skip_name else text.strip(),
                "phone": data.get("phone", ""),
                "preferred_date": data["day"],
                "preferred_time": data["time"],
                "expected_duration_minutes": str(data["duration"]),
                "expected_service_id": str(data["service_id"]),
            }
            commands.create_manual(
                self.session, form, now=kyiv_now(), actor=self.actor, locked=True
            )
            self.clear_state()
            self.day(date.fromisoformat(data["day"]))


def process_update(session: Session, update: dict) -> None:
    callback = update.get("callback_query")
    message = callback.get("message", {}) if callback else update.get("message", {})
    user = (callback or message).get("from", {})
    user_id = user.get("id")
    if (
        not isinstance(user_id, int)
        or user_id <= 0
        or user.get("is_bot")
        or message.get("chat", {}).get("type") != "private"
        or message.get("chat", {}).get("id") != user_id
    ):
        return
    bot = Bot(session, user_id, message_id=message.get("message_id") if callback else None)
    if callback:
        session.add(
            TelegramOutbox(
                method="answerCallbackQuery",
                payload={"callback_query_id": callback["id"]},
                telegram_id=user_id,
            )
        )
        action = session.get(TelegramAction, callback.get("data", "")[:40])
        if action is None or action.telegram_id != user_id or not unexpired(action.expires_at):
            bot.show("Ця кнопка застаріла. Відкрийте розклад ще раз командою /start.", edit=False)
            return
        data = action.payload.copy()
        session.delete(action)
        if data["op"] in {"skip_phone", "skip_name"}:
            if not bot.admin():
                return
            state = bot.state()
            expected = "phone" if data["op"] == "skip_phone" else "name"
            if (
                state is None
                or not unexpired(state.expires_at)
                or state.payload.get("mode") != expected
                or state.payload.get("flow") != data.get("flow")
            ):
                bot.show("Ця форма вже не діє. Оберіть годину ще раз.")
                return
            bot.manual_input(state, "", skip_name=data["op"] == "skip_name")
        else:
            bot.dispatch(data)
        return
    text = str(message.get("text") or "").strip()
    if not text:
        return
    command, _, arg = text.partition(" ")
    if command == "/start" and arg:
        bot.authenticate(arg, user)
        return
    if not bot.admin():
        return
    if command in {"/start", "/today", "/cancel"}:
        bot.clear_state()
        bot.day()
    elif command == "/records":
        bot.clear_state()
        bot.listing()
    elif command == "/help":
        bot.show(
            "/start — розклад огляду лікаря\n/records — усі записи\n/cancel — скасувати введення\n\nГортайте дні стрілками. ＋ додає пацієнта, × закриває годину, «Перенести» дозволяє вибрати новий день і час. 🟠 — нові, 🟢 — підтверджені. Зміни одразу з’являються на сайті."
        )
    else:
        state = bot.state()
        if state is None or not unexpired(state.expires_at):
            bot.show("Оберіть дію в розкладі: /start. Усі записи: /records.")
        else:
            bot.manual_input(state, text)


@router.post(TELEGRAM_WEBHOOK_PATH)
def webhook(
    update: dict[str, Any], request: Request, session: Annotated[Session, Depends(get_session)]
) -> dict:
    if not valid_telegram_webhook(request):
        raise HTTPException(403, "Доступ заборонено.")
    if not isinstance(update, dict) or type(update.get("update_id")) is not int:
        raise HTTPException(400, "Некоректний запит.")
    from app.telegram_refresh import chat_lock

    callback = update.get("callback_query", {})
    message = callback.get("message", {}) or update.get("message", {})
    telegram_id = (callback or message).get("from", {}).get("id", 0)
    if type(telegram_id) is not int or telegram_id < 0:
        telegram_id = 0
    with chat_lock(session.get_bind(), telegram_id):
        return apply_update(update, session, telegram_id)


def apply_update(update: dict, session: Session, telegram_id: int) -> dict:
    lock_booking_writes(session)
    if session.get(TelegramUpdate, update["update_id"]) is None:
        # Savepoint ensures a rejected command leaves no partial booking/state changes.
        try:
            with session.begin_nested():
                process_update(session, update)
        except HTTPException as exc:
            callback = update.get("callback_query", {})
            message = callback.get("message", {}) or update.get("message", {})
            user_id = (callback or message).get("from", {}).get("id")
            if (
                isinstance(user_id, int)
                and message.get("chat", {}).get("type") == "private"
                and message["chat"]["id"] == user_id
            ):
                if callback.get("id"):
                    session.add(
                        TelegramOutbox(
                            method="answerCallbackQuery",
                            payload={"callback_query_id": callback["id"]},
                            telegram_id=user_id,
                        )
                    )
                Bot(session, user_id).show(
                    html.escape(str(exc.detail)) + "\n\nОновити розклад: /start", edit=False
                )
        session.add(TelegramUpdate(update_id=update["update_id"]))
        for model in (TelegramAction, TelegramState, TelegramAuth):
            session.execute(delete(model).where(model.expires_at < utc_now()))
        session.execute(
            delete(TelegramUpdate).where(TelegramUpdate.created_at < utc_now() - timedelta(days=7))
        )
    session.commit()
    if not deliver_outbox(session, telegram_id=telegram_id):
        raise HTTPException(503, "Доставку відповіді буде повторено.")
    return {"ok": True}
