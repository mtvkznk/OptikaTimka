"""Link appointments to services and allow free-form administrative contact details."""

import sqlalchemy as sa

from alembic import op

revision = "20261117_0012"
down_revision = "20261115_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("appointment") as batch:
        batch.alter_column("name", existing_type=sa.String(120), type_=sa.Text(), existing_nullable=False)
        batch.alter_column("phone", existing_type=sa.String(40), type_=sa.Text(), existing_nullable=False)
        batch.add_column(sa.Column("service_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_appointment_service_id", "bookingservice", ["service_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_appointment_service_id", ["service_id"])

    op.execute(sa.text(
        "UPDATE appointment SET service_id = ("
        "SELECT id FROM bookingservice WHERE bookingservice.name = appointment.service)"
    ))


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM appointment LEFT JOIN bookingservice ON bookingservice.id = appointment.service_id "
        "WHERE length(appointment.name) > 120 OR length(appointment.phone) > 40 "
        "OR (appointment.service_id IS NOT NULL AND bookingservice.name <> appointment.service) LIMIT 1"
    )).scalar() is not None:
        raise RuntimeError("Downgrade would lose appointment contacts or service links.")
    with op.batch_alter_table("appointment") as batch:
        batch.drop_index("ix_appointment_service_id")
        batch.drop_constraint("fk_appointment_service_id", type_="foreignkey")
        batch.drop_column("service_id")
        batch.alter_column("name", existing_type=sa.Text(), type_=sa.String(120), existing_nullable=False)
        batch.alter_column("phone", existing_type=sa.Text(), type_=sa.String(40), existing_nullable=False)
