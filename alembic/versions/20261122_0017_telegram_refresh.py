"""Track the current Telegram timetable and safely retry background edits."""

import sqlalchemy as sa

from alembic import op

revision = "20261122_0017"
down_revision = "20261121_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegramview",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("generation", sa.String(40), nullable=False),
    )
    op.add_column("telegramoutbox", sa.Column("telegram_id", sa.BigInteger()))
    op.add_column("telegramoutbox", sa.Column("view_generation", sa.String(40)))
    op.add_column(
        "telegramoutbox",
        sa.Column("is_refresh", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("telegramoutbox", sa.Column("not_before", sa.DateTime(timezone=True)))
    op.create_index("ix_telegramoutbox_telegram_id", "telegramoutbox", ["telegram_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text("UPDATE telegramoutbox SET telegram_id = CAST(payload->>'chat_id' AS BIGINT)")
        )
    else:
        op.execute(
            sa.text("UPDATE telegramoutbox SET telegram_id = json_extract(payload, '$.chat_id')")
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM telegramoutbox WHERE is_refresh = true"))
    op.drop_table("telegramview")
    op.drop_index("ix_telegramoutbox_telegram_id", table_name="telegramoutbox")
    with op.batch_alter_table("telegramoutbox") as batch:
        for column in ("telegram_id", "view_generation", "is_refresh", "not_before"):
            batch.drop_column(column)
