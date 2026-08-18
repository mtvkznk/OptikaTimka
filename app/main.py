from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import Appointment, AppointmentCreate, AppointmentRead, Service, ServiceRead

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")
SessionDep = Annotated[Session, Depends(get_session)]


@app.get("/", response_class=HTMLResponse)
def home(request: Request, session: SessionDep) -> HTMLResponse:
    services = session.exec(select(Service).order_by(Service.sort_order)).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "services": services,
            "app_name": settings.app_name,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/api/services", response_model=list[ServiceRead])
def list_services(session: SessionDep) -> list[Service]:
    return session.exec(select(Service).order_by(Service.sort_order)).all()


@app.post(
    "/api/appointments",
    response_model=AppointmentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment(
    payload: AppointmentCreate,
    session: SessionDep,
) -> Appointment:
    if payload.service_id is not None:
        service = session.get(Service, payload.service_id)
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Послугу не знайдено.",
            )

    appointment = Appointment.model_validate(payload)
    session.add(appointment)

    try:
        session.commit()
        session.refresh(appointment)
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не вдалося створити заявку.",
        ) from exc

    return appointment
