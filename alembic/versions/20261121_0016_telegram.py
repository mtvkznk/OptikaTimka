"""Telegram identity, expiring interactions and transactional delivery."""

import sqlalchemy as sa

from alembic import op

revision = "20261121_0016"
down_revision = "20261118_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "customersession",
        sa.Column("provider", sa.String(16), nullable=False, server_default="google"),
    )
    op.create_table(
        "telegramaccount",
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_telegramaccount_telegram_id", "telegramaccount", ["telegram_id"], unique=True
    )
    op.create_table(
        "telegramauth",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("requester_id", sa.Integer(), sa.ForeignKey("customer.id", ondelete="CASCADE")),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("telegram_id", sa.BigInteger()),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_telegramauth_expires_at", "telegramauth", ["expires_at"])
    op.create_table(
        "telegramstate",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_telegramstate_expires_at", "telegramstate", ["expires_at"])
    op.create_table(
        "telegramaction",
        sa.Column("token", sa.String(40), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_telegramaction_expires_at", "telegramaction", ["expires_at"])
    op.create_table(
        "telegramupdate",
        sa.Column("update_id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_telegramupdate_created_at", "telegramupdate", ["created_at"])
    op.create_table(
        "telegramoutbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("admin_customer_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_telegramoutbox_created_at", "telegramoutbox", ["created_at"])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM customersession WHERE provider = 'telegram'"))
    for table in (
        "telegramoutbox",
        "telegramupdate",
        "telegramaction",
        "telegramstate",
        "telegramauth",
        "telegramaccount",
    ):
        op.drop_table(table)
    with op.batch_alter_table("customersession") as batch:
        batch.drop_column("provider")
