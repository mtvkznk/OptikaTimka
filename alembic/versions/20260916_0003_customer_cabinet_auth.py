"""Customer cabinet auth.

Revision ID: 20260916_0003
Revises: 20260911_0002
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260916_0003"
down_revision: str | Sequence[str] | None = "20260911_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("google_sub", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("google_sub"),
        sa.UniqueConstraint("phone"),
    )
    op.create_index("ix_customer_email", "customer", ["email"])
    op.create_index("ix_customer_google_sub", "customer", ["google_sub"])
    op.create_index("ix_customer_phone", "customer", ["phone"])

    op.create_table(
        "phoneauthcode",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_phoneauthcode_code_hash", "phoneauthcode", ["code_hash"])
    op.create_index("ix_phoneauthcode_created_at", "phoneauthcode", ["created_at"])
    op.create_index("ix_phoneauthcode_expires_at", "phoneauthcode", ["expires_at"])
    op.create_index("ix_phoneauthcode_phone", "phoneauthcode", ["phone"])

    op.create_table(
        "customersession",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_customersession_created_at", "customersession", ["created_at"])
    op.create_index("ix_customersession_customer_id", "customersession", ["customer_id"])
    op.create_index("ix_customersession_expires_at", "customersession", ["expires_at"])
    op.create_index("ix_customersession_token_hash", "customersession", ["token_hash"])

    with op.batch_alter_table("appointment") as batch_op:
        batch_op.add_column(sa.Column("customer_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_appointment_customer_id_customer",
            "customer",
            ["customer_id"],
            ["id"],
        )
        batch_op.create_index("ix_appointment_customer_id", ["customer_id"])


def downgrade() -> None:
    with op.batch_alter_table("appointment") as batch_op:
        batch_op.drop_index("ix_appointment_customer_id")
        batch_op.drop_constraint("fk_appointment_customer_id_customer", type_="foreignkey")
        batch_op.drop_column("customer_id")

    op.drop_index("ix_customersession_token_hash", table_name="customersession")
    op.drop_index("ix_customersession_expires_at", table_name="customersession")
    op.drop_index("ix_customersession_customer_id", table_name="customersession")
    op.drop_index("ix_customersession_created_at", table_name="customersession")
    op.drop_table("customersession")

    op.drop_index("ix_phoneauthcode_phone", table_name="phoneauthcode")
    op.drop_index("ix_phoneauthcode_expires_at", table_name="phoneauthcode")
    op.drop_index("ix_phoneauthcode_created_at", table_name="phoneauthcode")
    op.drop_index("ix_phoneauthcode_code_hash", table_name="phoneauthcode")
    op.drop_table("phoneauthcode")

    op.drop_index("ix_customer_phone", table_name="customer")
    op.drop_index("ix_customer_google_sub", table_name="customer")
    op.drop_index("ix_customer_email", table_name="customer")
    op.drop_table("customer")
