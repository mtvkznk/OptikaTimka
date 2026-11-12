"""Remove the unused table for one-time phone login codes.

Revision ID: 20261112_0008
Revises: 20261112_0007
"""

import sqlalchemy as sa

from alembic import op

revision = "20261112_0008"
down_revision = "20261112_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("phoneauthcode")


def downgrade() -> None:
    op.create_table(
        "phoneauthcode",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("phone", "code_hash", "created_at", "expires_at"):
        op.create_index(f"ix_phoneauthcode_{column}", "phoneauthcode", [column])
