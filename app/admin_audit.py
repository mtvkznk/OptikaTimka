from sqlmodel import Session

from app.models import AdminAuditEvent

AUDIT_LABELS = {
    "schedule_changed": "Змінено графік",
    "slot_closed": "Закрито години запису",
    "slot_reopened": "Відкрито години запису",
    "appointment_deleted": "Видалено прийом",
}


def add_admin_audit(session: Session, actor: str, action: str, detail: str) -> None:
    session.add(AdminAuditEvent(actor=actor[:255], action=action, detail=detail))
