from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.database import get_session
from app.main import app

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SQLModel.metadata.create_all(engine)


def make_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


app.dependency_overrides[get_session] = make_session
client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root_describes_backend_stack() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["stack"] == ["FastAPI", "SQLModel", "Alembic", "PostgreSQL"]


def test_create_and_list_product() -> None:
    create_response = client.post(
        "/api/products",
        json={
            "category": "frames",
            "name": "Classic frame",
            "description": "Lightweight everyday frame.",
            "price_cents": 120000,
            "currency": "UAH",
            "sort_order": 1,
        },
    )

    assert create_response.status_code == 201
    assert create_response.json()["name"] == "Classic frame"

    list_response = client.get("/api/products")

    assert list_response.status_code == 200
    assert list_response.json()[0]["name"] == "Classic frame"


def test_create_appointment() -> None:
    response = client.post(
        "/api/appointments",
        json={
            "name": "Ivan Petrenko",
            "phone": "+380980000000",
            "email": "ivan@example.com",
            "service": "Vision check",
            "preferred_date": "2026-09-12",
            "preferred_time": "10:30:00",
            "message": "Need a consultation.",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "new"

