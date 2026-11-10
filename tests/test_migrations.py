import os
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import create_engine

from alembic import command


@pytest.fixture
def migration_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[Config, Engine]]:
    postgres_url = os.environ.get("TEST_DATABASE_URL")
    if postgres_url:
        schema = "optika_migration_" + uuid.uuid4().hex
        admin_engine = create_engine(postgres_url)
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        url = sa.engine.make_url(postgres_url).update_query_dict({"options": f"-csearch_path={schema}"})
        engine = create_engine(url)
        database_url = url.render_as_string(hide_password=False)
    else:
        database_url = f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"
        engine = create_engine(database_url)
    from app.config import settings
    monkeypatch.setattr(settings, "database_url", database_url)
    config = Config("alembic.ini")
    try:
        yield config, engine
    finally:
        engine.dispose()
        if postgres_url:
            with admin_engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin_engine.dispose()


def insert_legacy_data(engine: Engine) -> None:
    now = "2026-11-10 12:00:00"
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO customer (id, name, phone, email, google_sub, created_at, updated_at) "
            "VALUES (1, 'Google client', '+380980000001', 'google@example.com', 'google-sub', :now, :now),"
            "(2, 'Phone client', '+380980000002', 'unverified@example.com', NULL, :now, :now)"
        ), {"now": now})
        connection.execute(sa.text(
            "INSERT INTO customersession (customer_id, token_hash, expires_at, created_at) "
            "VALUES (1, 'old-token', :now, :now)"
        ), {"now": now})
        connection.execute(sa.text(
            "INSERT INTO appointment (name, phone, email, service, preferred_date, preferred_time, "
            "message, status, created_at, updated_at) VALUES "
            "('Existing appointment', '+38 (098) 000-00-01', 'GOOGLE@EXAMPLE.COM', 'Exam', "
            "'2026-11-12', '10:00:00', '', 'new', :now, :now)"
        ), {"now": now})


def test_upgrade_preserves_appointments_and_removes_unverified_identity_claims(
    migration_database: tuple[Config, Engine],
) -> None:
    config, engine = migration_database
    command.upgrade(config, "20260916_0003")
    insert_legacy_data(engine)
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(sa.text("SELECT * FROM appointment")).mappings().one()
        assert row["name"] == "Existing appointment"
        assert row["phone"] == "+38 (098) 000-00-01"
        assert row["phone_normalized"] == "+380980000001"
        assert row["email_normalized"] == "google@example.com"
        customers = connection.execute(sa.text("SELECT * FROM customer ORDER BY id")).mappings().all()
        assert customers[0]["phone"] is None
        assert customers[1]["email"] is None
        assert not customers[0]["email_verified"]
        assert connection.execute(sa.text("SELECT COUNT(*) FROM customersession")).scalar_one() == 0
    command.downgrade(config, "20260916_0003")
    command.upgrade(config, "head")


def test_migration_refuses_to_silently_cancel_duplicate_bookings(
    migration_database: tuple[Config, Engine],
) -> None:
    config, engine = migration_database
    command.upgrade(config, "20260916_0003")
    insert_legacy_data(engine)
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO appointment (name, phone, service, preferred_date, preferred_time, "
            "message, status, created_at, updated_at) "
            "SELECT name, phone, service, preferred_date, preferred_time, message, status, "
            "created_at, updated_at FROM appointment"
        ))
    with pytest.raises(RuntimeError, match="Resolve duplicate bookings"):
        command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM appointment")).scalar_one() == 2
