"""Store individual booking blocks per service."""

import sqlalchemy as sa

from alembic import op

revision = "20261118_0014"
down_revision = "20261117_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bookingblock",
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=False),
        sa.Column("end_minute", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["bookingservice.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("service_id", "day", "start_minute"),
        sa.CheckConstraint(
            "start_minute >= 0 AND start_minute < end_minute AND end_minute <= 1440",
            name="ck_booking_block_interval",
        ),
    )


def downgrade() -> None:
    op.drop_table("bookingblock")
