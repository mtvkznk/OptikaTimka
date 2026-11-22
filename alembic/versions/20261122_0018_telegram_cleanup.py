"""Remember known workflow message IDs for private-chat cleanup."""

import sqlalchemy as sa

from alembic import op

revision = "20261122_0018"
down_revision = "20261122_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegramchat",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("generation", sa.String(40), nullable=False),
        sa.Column("delivered_generation", sa.String(40), nullable=False),
        sa.Column("cleanup_after", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "telegrammessage",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("message_id", sa.BigInteger(), primary_key=True),
        sa.Column("is_ui", sa.Boolean(), nullable=False),
        sa.Column("pending", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("telegramoutbox", sa.Column("screen_generation", sa.String(40)))
    # Protect the existing timetable; do not guess IDs or ages of past chat messages.
    op.execute(
        sa.text(
            "INSERT INTO telegramchat (telegram_id, message_id, generation, delivered_generation) SELECT telegram_id, message_id, generation, generation FROM telegramview"
        )
    )


def downgrade() -> None:
    op.drop_table("telegrammessage")
    op.drop_table("telegramchat")
    with op.batch_alter_table("telegramoutbox") as batch:
        batch.drop_column("screen_generation")
