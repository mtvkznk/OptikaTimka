"""Add working hours per service and weekday."""

import sqlalchemy as sa

from alembic import op

revision = "20261117_0013"
down_revision = "20261117_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "serviceweekdayschedule",
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("intervals", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["bookingservice.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("service_id", "weekday"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_service_schedule_weekday"),
    )


def downgrade() -> None:
    op.drop_table("serviceweekdayschedule")
