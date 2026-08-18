"""Initial schema.

Revision ID: 20260818_0001
Revises:
Create Date: 2026-08-18
"""

import sqlalchemy as sa

from alembic import op

revision = "20260818_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("price_uah", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "appointment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(length=800), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["service.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appointment_created_at", "appointment", ["created_at"])
    op.create_index("ix_service_sort_order", "service", ["sort_order"])

    op.bulk_insert(
        sa.table(
            "service",
            sa.column("title", sa.String),
            sa.column("description", sa.String),
            sa.column("price_uah", sa.Integer),
            sa.column("sort_order", sa.Integer),
        ),
        [
            {
                "title": "Діагностика зору",
                "description": "Повне обстеження зору на сучасному обладнанні.",
                "price_uah": 800,
                "sort_order": 1,
            },
            {
                "title": "Підбір окулярів",
                "description": "Індивідуальний підбір окулярів для максимальної чіткості.",
                "price_uah": 600,
                "sort_order": 2,
            },
            {
                "title": "Підбір контактних лінз",
                "description": "Професійний підбір контактних лінз з урахуванням ваших очей.",
                "price_uah": 700,
                "sort_order": 3,
            },
            {
                "title": "Лікування захворювань очей",
                "description": "Діагностика та лікування захворювань очей різної складності.",
                "price_uah": 900,
                "sort_order": 4,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_service_sort_order", table_name="service")
    op.drop_index("ix_appointment_created_at", table_name="appointment")
    op.drop_table("appointment")
    op.drop_table("service")
