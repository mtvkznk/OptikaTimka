"""Add service weekdays and editable catalog category names.

Revision ID: 20261112_0006
Revises: 20261111_0005
"""

import sqlalchemy as sa

from alembic import op

revision = "20261112_0006"
down_revision = "20261111_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bookingservice",
        sa.Column("weekdays_mask", sa.Integer(), server_default="127", nullable=False),
    )
    op.create_table(
        "catalogcategory",
        sa.Column("slug", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("catalogcategory")
    op.drop_column("bookingservice", "weekdays_mask")
