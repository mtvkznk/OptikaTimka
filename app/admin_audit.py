from sqlmodel import Session

from app.models import AdminAuditEvent

AUDIT_LABELS = {
    "appointment_created": "Додано прийом",
    "appointment_contact_updated": "Змінено дані пацієнта в записі",
    "appointment_status": "Змінено статус прийому",
    "telegram_linked": "Прив’язано Telegram",
    "telegram_unlinked": "Від’єднано Telegram",
    "schedule_changed": "Змінено графік",
    "slot_closed": "Закрито години запису",
    "slot_reopened": "Відкрито години запису",
    "appointment_deleted": "Видалено прийом",
    "appointment_moved": "Перенесено прийом",
}


def add_admin_audit(session: Session, actor: str, action: str, detail: str) -> None:
    session.add(AdminAuditEvent(actor=actor[:255], action=action, detail=detail))
