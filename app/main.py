from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import (
    AnalyticsEvent,
    AnalyticsEventCreate,
    AnalyticsEventRead,
    Appointment,
    AppointmentCreate,
    AppointmentRead,
    ContentBlock,
    ContentBlockRead,
    Product,
    ProductCreate,
    ProductRead,
)

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
SessionDep = Annotated[Session, Depends(get_session)]

SERVICES = [
    "Перевірка зору",
    "Підбір окулярів",
    "Підбір контактних лінз",
    "Консультація офтальмолога",
]

TIME_SLOTS = ["09:30", "10:30", "12:00", "14:30", "16:00", "18:00"]


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "services": SERVICES,
            "time_slots": TIME_SLOTS,
        },
    )


@app.get("/api/status")
def api_status() -> dict[str, object]:
    return {
        "app": settings.app_name,
        "status": "backend-ready",
        "stack": ["FastAPI", "SQLModel", "Alembic", "PostgreSQL"],
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/api/content-blocks", response_model=list[ContentBlockRead])
def list_content_blocks(session: SessionDep) -> list[ContentBlock]:
    return list(
        session.exec(
            select(ContentBlock)
            .where(ContentBlock.is_visible)
            .order_by(ContentBlock.sort_order, ContentBlock.id)
        ).all()
    )


@app.get("/api/products", response_model=list[ProductRead])
def list_products(session: SessionDep) -> list[Product]:
    return list(
        session.exec(
            select(Product)
            .where(Product.is_active)
            .order_by(Product.sort_order, Product.id)
        ).all()
    )


@app.post("/api/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, session: SessionDep) -> Product:
    product = Product.model_validate(payload)
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


@app.post("/api/appointments", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
def create_appointment(payload: AppointmentCreate, session: SessionDep) -> Appointment:
    appointment = Appointment.model_validate(payload)
    session.add(appointment)
    session.commit()
    session.refresh(appointment)
    return appointment


@app.post(
    "/api/analytics/events",
    response_model=AnalyticsEventRead,
    status_code=status.HTTP_201_CREATED,
)
def record_analytics_event(
    payload: AnalyticsEventCreate,
    session: SessionDep,
) -> AnalyticsEvent:
    event = AnalyticsEvent.model_validate(payload)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event
