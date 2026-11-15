"""Store category positions and permanent removals."""

import sqlalchemy as sa

from alembic import op

revision = "20261115_0011"
down_revision = "20261114_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("catalogcategory", sa.Column("sort_order", sa.Integer(), nullable=True))
    op.add_column(
        "catalogcategory",
        sa.Column("is_purged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("catalogcategory", "is_purged")
    op.drop_column("catalogcategory", "sort_order")
