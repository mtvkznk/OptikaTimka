"""Store the duration reserved by each appointment.

Revision ID: 20261111_0005
Revises: 20261110_0004
"""

from datetime import time

import sqlalchemy as sa

from alembic import op

revision = "20261111_0005"
down_revision = "20261110_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "appointment",
        sa.Column("duration_minutes", sa.Integer(), server_default="60", nullable=False),
    )
    connection = op.get_bind()
    appointment = sa.table(
        "appointment",
        sa.column("id", sa.Integer),
        sa.column("service", sa.String),
        sa.column("preferred_time", sa.Time),
        sa.column("duration_minutes", sa.Integer),
    )
    services = dict(connection.execute(
        sa.text("SELECT name, duration_minutes FROM bookingservice")
    ).tuples().all())
    slots = {}
    for start, end in connection.execute(sa.text("SELECT start_time, end_time FROM availabletimeslot")):
        if isinstance(start, str):
            start, end = time.fromisoformat(start), time.fromisoformat(end)
        slots[start] = (end.hour - start.hour) * 60 + end.minute - start.minute

    # Old bookings occupied the configured block at their start time.
    last_id = 0
    while rows := connection.execute(
        sa.select(appointment.c.id, appointment.c.service, appointment.c.preferred_time)
        .where(appointment.c.id > last_id)
        .order_by(appointment.c.id)
        .limit(1000)
    ).all():
        for appointment_id, service, start in rows:
            duration = max(slots.get(start, 60), services.get(service, 0))
            if duration != 60:
                connection.execute(
                    appointment.update().where(appointment.c.id == appointment_id)
                    .values(duration_minutes=duration)
                )
        last_id = rows[-1].id


def downgrade() -> None:
    op.drop_column("appointment", "duration_minutes")
