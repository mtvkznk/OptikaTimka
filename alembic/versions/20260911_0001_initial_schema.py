"""Initial schema.

Revision ID: 20260911_0001
Revises:
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260911_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contentblock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("nav_label", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_contentblock_is_visible", "contentblock", ["is_visible"])
    op.create_index("ix_contentblock_slug", "contentblock", ["slug"])
    op.create_index("ix_contentblock_sort_order", "contentblock", ["sort_order"])

    op.create_table(
        "product",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_category", "product", ["category"])
    op.create_index("ix_product_is_active", "product", ["is_active"])
    op.create_index("ix_product_sort_order", "product", ["sort_order"])

    op.create_table(
        "appointment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("service", sa.String(length=160), nullable=False),
        sa.Column("preferred_date", sa.Date(), nullable=True),
        sa.Column("preferred_time", sa.Time(), nullable=True),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appointment_created_at", "appointment", ["created_at"])
    op.create_index("ix_appointment_status", "appointment", ["status"])

    op.create_table(
        "analyticsevent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analyticsevent_created_at", "analyticsevent", ["created_at"])
    op.create_index("ix_analyticsevent_event_type", "analyticsevent", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_analyticsevent_event_type", table_name="analyticsevent")
    op.drop_index("ix_analyticsevent_created_at", table_name="analyticsevent")
    op.drop_table("analyticsevent")
    op.drop_index("ix_appointment_status", table_name="appointment")
    op.drop_index("ix_appointment_created_at", table_name="appointment")
    op.drop_table("appointment")
    op.drop_index("ix_product_sort_order", table_name="product")
    op.drop_index("ix_product_is_active", table_name="product")
    op.drop_index("ix_product_category", table_name="product")
    op.drop_table("product")
    op.drop_index("ix_contentblock_sort_order", table_name="contentblock")
    op.drop_index("ix_contentblock_slug", table_name="contentblock")
    op.drop_index("ix_contentblock_is_visible", table_name="contentblock")
    op.drop_table("contentblock")

