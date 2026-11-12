"""Allow catalog categories to be removed from the storefront.

Revision ID: 20261112_0007
Revises: 20261112_0006
"""

import sqlalchemy as sa

from alembic import op

revision = "20261112_0007"
down_revision = "20261112_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalogcategory",
        sa.Column("is_removed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("catalogcategory", "is_removed")
