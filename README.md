# Optika Timka

Clean backend foundation for the Optika Timka website.

## Stack

- FastAPI
- SQLModel
- Alembic
- PostgreSQL

## Local Setup

1. Create a virtual environment.
2. Install the project with dev dependencies.
3. Copy `.env.example` to `.env`.
4. Start PostgreSQL with Docker Compose.
5. Run Alembic migrations.
6. Start FastAPI.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d db
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

Open `http://127.0.0.1:8000/` to view the appointment form. Backend stack status is available at `http://127.0.0.1:8000/api/status`.

## Development Checks

```powershell
.\.venv\Scripts\pytest.exe
.\.venv\Scripts\ruff.exe check .
```
