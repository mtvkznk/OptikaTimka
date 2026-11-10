"""Verified identities, shared rate limits, and exclusive appointment slots.

Revision ID: 20261110_0004
Revises: 20260916_0003
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261110_0004"
down_revision: str | Sequence[str] | None = "20260916_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            "SELECT 1 FROM appointment WHERE status != 'canceled' "
            "AND preferred_date IS NOT NULL AND preferred_time IS NOT NULL "
            "GROUP BY preferred_date, preferred_time HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate:
        raise RuntimeError(
            "Active appointments share a date/time slot. Resolve duplicate bookings "
            "before upgrading; this migration never cancels appointments automatically."
        )

    with op.batch_alter_table("customer") as batch:
        batch.add_column(
            sa.Column(
                "phone_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "email_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    # Earlier releases copied unverified booking contacts into customer accounts.
    # Preserve the original appointments, discard unproven identity claims, and
    # require a fresh login before granting access to historical appointments.
    connection.execute(sa.text("UPDATE customer SET phone = NULL WHERE google_sub IS NOT NULL"))
    connection.execute(sa.text("UPDATE customer SET email = NULL WHERE google_sub IS NULL"))
    connection.execute(sa.text("DELETE FROM customersession"))
    connection.execute(sa.text("DELETE FROM phoneauthcode"))

    with op.batch_alter_table("appointment") as batch:
        batch.add_column(sa.Column("phone_normalized", sa.String(40), nullable=True))
        batch.add_column(sa.Column("email_normalized", sa.String(255), nullable=True))
        batch.create_index("ix_appointment_phone_normalized", ["phone_normalized"])
        batch.create_index("ix_appointment_email_normalized", ["email_normalized"])
    appointment = sa.table(
        "appointment",
        sa.column("id"),
        sa.column("phone"),
        sa.column("email"),
        sa.column("phone_normalized"),
        sa.column("email_normalized"),
    )
    # Keyset batches keep migration memory bounded and do not rewrite display data.
    last_id = 0
    while True:
        rows = (
            connection.execute(
                sa.select(
                    appointment.c.id,
                    appointment.c.phone,
                    appointment.c.email,
                )
                .where(appointment.c.id > last_id)
                .order_by(appointment.c.id)
                .limit(500)
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        for row in rows:
            raw = re.sub(r"[\s().-]", "", (row["phone"] or "").strip())
            if raw.startswith("00"):
                raw = "+" + raw[2:]
            phone = "+" + raw.lstrip("+") if re.fullmatch(r"\+?[1-9][0-9]{7,14}", raw) else None
            email = (row["email"] or "").strip().lower() or None
            connection.execute(
                appointment.update()
                .where(appointment.c.id == row["id"])
                .values(
                    phone_normalized=phone,
                    email_normalized=email,
                )
            )
        last_id = rows[-1]["id"]

    op.create_index(
        "uq_appointment_active_slot",
        "appointment",
        ["preferred_date", "preferred_time"],
        unique=True,
        sqlite_where=sa.text("status != 'canceled'"),
        postgresql_where=sa.text("status != 'canceled'"),
    )
    op.create_table(
        "securityratelimit",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_securityratelimit_expires_at", "securityratelimit", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_securityratelimit_expires_at", table_name="securityratelimit")
    op.drop_table("securityratelimit")
    op.drop_index("uq_appointment_active_slot", table_name="appointment")
    with op.batch_alter_table("appointment") as batch:
        batch.drop_index("ix_appointment_email_normalized")
        batch.drop_index("ix_appointment_phone_normalized")
        batch.drop_column("email_normalized")
        batch.drop_column("phone_normalized")
    with op.batch_alter_table("customer") as batch:
        batch.drop_column("email_verified")
        batch.drop_column("phone_verified")
