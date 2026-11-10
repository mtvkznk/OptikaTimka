import os
import re
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.config import settings
from app.database import get_session
from app.main import app

ADMIN_AUTH = ("admin", "test-only-admin-password-123")


class BrowserClient(TestClient):
    """Read the actual page token, as the browser does, before each mutation."""

    def request(self, method: str, url: Any, **kwargs: Any) -> Any:
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            page = super().request("GET", "/cabinet")
            token = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
            assert token is not None
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("X-CSRF-Token", token.group(1))
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@pytest.fixture
def engine(tmp_path: Path) -> Generator[Engine]:
    postgres_url = os.environ.get("TEST_DATABASE_URL")
    if postgres_url:
        schema = "optika_test_" + uuid.uuid4().hex
        admin_engine = create_engine(postgres_url)
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        test_engine = create_engine(
            postgres_url, connect_args={"options": f"-csearch_path={schema}"}
        )
    else:
        test_engine = create_engine(
            f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )

        @event.listens_for(test_engine, "connect")
        def enable_foreign_keys(connection: Any, record: Any) -> None:
            connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        test_engine.dispose()
        if postgres_url:
            with admin_engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin_engine.dispose()


@pytest.fixture
def app_db(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Generator[Engine]:
    def make_session() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = make_session
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "admin_username", ADMIN_AUTH[0])
    monkeypatch.setattr(settings, "admin_password", SecretStr(ADMIN_AUTH[1]))
    monkeypatch.setattr(settings, "admin_google_email", None)
    monkeypatch.setattr(settings, "phone_auth_debug", True)
    monkeypatch.setattr(settings, "public_base_url", None)
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)
    monkeypatch.setattr(settings, "google_redirect_uri", None)
    try:
        yield engine
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(app_db: Engine) -> Generator[BrowserClient]:
    with BrowserClient(app) as browser:
        browser.auth = ADMIN_AUTH
        yield browser


@pytest.fixture
def anonymous_client(app_db: Engine) -> Generator[BrowserClient]:
    with BrowserClient(app) as browser:
        yield browser


@pytest.fixture
def raw_client(app_db: Engine) -> Generator[TestClient]:
    with TestClient(app) as browser:
        yield browser
