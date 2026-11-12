"""Store product photos with the catalog.

Revision ID: 20261112_0009
Revises: 20261112_0008
"""

import sqlalchemy as sa

from alembic import op

revision = "20261112_0009"
down_revision = "20261112_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "productphoto",
        sa.Column(
            "product_id", sa.Integer(), sa.ForeignKey("product.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("jpeg", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("productphoto")
