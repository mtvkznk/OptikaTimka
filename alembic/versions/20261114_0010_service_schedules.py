"""Add optional working hours and closed dates for each service."""

import sqlalchemy as sa

from alembic import op

revision = "20261114_0010"
down_revision = "20261112_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "serviceschedule",
        sa.Column("service_id", sa.Integer(), primary_key=True),
        sa.Column("intervals", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["bookingservice.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "servicecloseddate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("closed_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(160), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["bookingservice.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("service_id", "closed_on"),
    )
    op.create_index("ix_servicecloseddate_closed_on", "servicecloseddate", ["closed_on"])


def downgrade() -> None:
    op.drop_table("servicecloseddate")
    op.drop_table("serviceschedule")
