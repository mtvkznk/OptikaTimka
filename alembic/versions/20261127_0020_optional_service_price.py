"""Allow booking services to be shown without a price."""

import sqlalchemy as sa

from alembic import op

revision = "20261127_0020"
down_revision = "20261122_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bookingservice") as batch:
        batch.alter_column("price_uah", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM bookingservice WHERE price_uah IS NULL LIMIT 1"
    )).scalar() is not None:
        raise RuntimeError("Downgrade would lose services without a price.")
    with op.batch_alter_table("bookingservice") as batch:
        batch.alter_column("price_uah", existing_type=sa.Integer(), nullable=False)
