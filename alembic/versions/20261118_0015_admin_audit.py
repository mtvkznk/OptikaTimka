"""Keep an administrator activity trail."""

import sqlalchemy as sa

from alembic import op

revision = "20261118_0015"
down_revision = "20261118_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adminauditevent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
    )
    op.create_index("ix_adminauditevent_created_id", "adminauditevent", ["created_at", "id"])


def downgrade() -> None:
    op.drop_table("adminauditevent")
