from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.database import get_session
from app.main import app
from app.models import Service


def make_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Service(
                title="Діагностика зору",
                description="Тестова послуга",
                price_uah=800,
                sort_order=1,
            )
        )
        session.commit()
    with Session(engine) as session:
        yield session


app.dependency_overrides[get_session] = make_session
client = TestClient(app)


def test_homepage_renders() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "ОптикаTimka" in response.text
    assert "Офтальмологія" in response.text


def test_create_appointment() -> None:
    response = client.post(
        "/api/appointments",
        json={
            "name": "Іван",
            "phone": "+38 (098) 000 00 00",
            "service_id": 1,
            "message": "Потрібна консультація",
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Іван"
