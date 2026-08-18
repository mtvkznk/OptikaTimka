# Оптика Timka

FastAPI-сайт для оптики та офтальмології з SQLModel, Alembic і PostgreSQL.

## Стек

- FastAPI
- SQLModel
- Alembic
- PostgreSQL
- Jinja2 templates

## Запуск локально

1. Створіть `.env` з прикладу:

```bash
cp .env.example .env
```

2. Підніміть PostgreSQL:

```bash
docker compose up -d db
```

3. Встановіть залежності:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

4. Запустіть міграції:

```bash
alembic upgrade head
```

5. Запустіть сайт:

```bash
uvicorn app.main:app --reload
```

Сайт буде доступний на `http://127.0.0.1:8000`.

## API

- `GET /` — головна сторінка.
- `GET /health` — перевірка стану.
- `GET /api/services` — список послуг.
- `POST /api/appointments` — створення заявки на прийом.
