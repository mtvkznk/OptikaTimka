"""Admin booking controls.

Revision ID: 20260911_0002
Revises: 20260911_0001
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260911_0002"
down_revision: str | Sequence[str] | None = "20260911_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bookingservice",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("price_uah", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_bookingservice_is_active", "bookingservice", ["is_active"])
    op.create_index("ix_bookingservice_sort_order", "bookingservice", ["sort_order"])

    op.create_table(
        "availabletimeslot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("start_time"),
    )
    op.create_index("ix_availabletimeslot_is_active", "availabletimeslot", ["is_active"])
    op.create_index("ix_availabletimeslot_sort_order", "availabletimeslot", ["sort_order"])

    op.create_table(
        "closeddate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("closed_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("closed_on"),
    )
    op.create_index("ix_closeddate_closed_on", "closeddate", ["closed_on"])


def downgrade() -> None:
    op.drop_index("ix_closeddate_closed_on", table_name="closeddate")
    op.drop_table("closeddate")
    op.drop_index("ix_availabletimeslot_sort_order", table_name="availabletimeslot")
    op.drop_index("ix_availabletimeslot_is_active", table_name="availabletimeslot")
    op.drop_table("availabletimeslot")
    op.drop_index("ix_bookingservice_sort_order", table_name="bookingservice")
    op.drop_index("ix_bookingservice_is_active", table_name="bookingservice")
    op.drop_table("bookingservice")
