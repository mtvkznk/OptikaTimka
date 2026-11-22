"""Remove Telegram website login; retain Google sessions and administrator bot links."""

import sqlalchemy as sa

from alembic import op

revision = "20261122_0019"
down_revision = "20261122_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM customersession WHERE provider <> 'google'"))
    with op.batch_alter_table("customersession") as batch:
        batch.drop_column("provider")
    op.drop_table("telegramauth")
    op.create_table(
        "telegramlink",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_telegramlink_customer_id", "telegramlink", ["customer_id"])
    op.create_index("ix_telegramlink_expires_at", "telegramlink", ["expires_at"])
    # Remove only former Telegram-only bindings. Their customers/appointments stay intact.
    op.execute(
        sa.text(
            "DELETE FROM telegramaccount WHERE customer_id IN (SELECT id FROM customer WHERE google_sub IS NULL OR email_verified = false)"
        )
    )
    expression = (
        "payload->>'op'"
        if op.get_bind().dialect.name == "postgresql"
        else "json_extract(payload, '$.op')"
    )
    op.execute(sa.text(f"DELETE FROM telegramaction WHERE {expression} IN ('auth', 'auth_cancel')"))


def downgrade() -> None:
    op.drop_table("telegramlink")
    op.add_column(
        "customersession",
        sa.Column("provider", sa.String(16), nullable=False, server_default="google"),
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
