# Optika Timka

FastAPI website for appointment booking, a customer cabinet, and an administrator dashboard.
Python 3.12+, SQLModel, Alembic, PostgreSQL, Jinja2, and plain CSS/JavaScript.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d db
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Set `ADMIN_PASSWORD` in your local `.env` before opening `/admin`. Administrator
pages and product creation require HTTP Basic authentication; no default password
or local authentication bypass exists. An unconfigured administrator receives 503.
Customers sign in with Google. Appointment booking also works without signing in
and uses the phone number for contact.

For a SQLite preview without Docker, set `DATABASE_URL=sqlite:///preview.db`, run
the same Alembic command, then start Uvicorn. SQLite previews do not replace
PostgreSQL integration tests.

Open `/` for the home page, `/booking` for the appointment form, `/catalog` for
products, and `/cabinet` for customer login. `/health` reports process health.
`/api/status` describes the application stack.

### Enable Google sign-in

Create a **Web application** OAuth client in [Google Cloud](https://console.cloud.google.com/auth/clients).
For the local Uvicorn command above, register
`http://127.0.0.1:8000/auth/google/callback` as an authorized redirect URI. Use
`127.0.0.1:8000` when opening the local site so the login and callback share a host.

Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and the matching
`GOOGLE_REDIRECT_URI` in the local `.env`, then restart Uvicorn and try Google
sign-in from `/cabinet`. Keep credentials in the ignored environment file.
For production, register `https://YOUR-DOMAIN/auth/google/callback` and set the
credentials in `prod.env`. The callback defaults to `PUBLIC_BASE_URL` plus
`/auth/google/callback` when `GOOGLE_REDIRECT_URI` is empty.

### Restrict administration to specific Google accounts

Set `ADMIN_GOOGLE_EMAIL` in `prod.env` (or local `.env`) to the owner's exact email.
To grant access to another account that has already signed in through Google, add
its stored `customer.google_sub` to `ADMIN_GOOGLE_SUBJECTS` as a comma-separated list
of Google account IDs. Both settings require Google OAuth credentials and disable
HTTP Basic for all administrator routes and product creation. Authorized users
see the administrator dashboard button in their Google-authenticated cabinet.

## Security behavior

- All `/admin` routes and `POST /api/products` require an authorized Google account,
  or HTTP Basic when neither Google administrator setting is configured. Use HTTPS.
- State-changing requests require a signed, session-bound CSRF token. HTML forms
  carry `csrf_token`; JavaScript reads the `csrf-token` meta tag and sends the
  `X-CSRF-Token` header. Cross-origin mutations are rejected as well.
- Customer records are selected by account ownership or verified contact details
  on unclaimed appointments. Booking fields never establish verified identity.
- Google login requires a verified email, uses the stable Google `sub`, validates
  OAuth state, uses PKCE, and refuses provider redirects.
- Production cookies are Secure, HttpOnly, and host-prefixed. Dynamic responses
  use `Cache-Control: no-store`; CSP, frame restrictions, and other security headers
  apply to responses. Production OpenAPI/Swagger/ReDoc endpoints are disabled.
- Request bodies are limited to 64 KiB. Persistent database rate limits work across
  application processes. Raw IP addresses are not stored in rate-limit keys.
- One active appointment can occupy any minute of the schedule. The service duration
  is stored on each booking, and booking writes and reactivations check for overlap
  under a database transaction lock. Canceling releases the reserved time.
  `/api/availability?preferred_date=YYYY-MM-DD&service=NAME` returns start/end pairs
  that fit the selected service, without appointment details. Customer and
  administrator appointment lists use 50-row pages.

## Production preparation

The production server is not needed for tests. Do not copy local `.env` files,
SQLite preview databases, or debug settings to production.

1. Copy `prod.env.example` **outside this repository** to
   `C:/Deploy/OptikaTimka/prod.env`, or set `OPTIKA_ENV_FILE` to its actual path.
2. Set `PUBLIC_BASE_URL` to your public HTTPS origin. Set independent random
   `AUTH_SECRET` (at least 32 characters) and `POSTGRES_PASSWORD`. Configure
   `ADMIN_GOOGLE_EMAIL` or `ADMIN_GOOGLE_SUBJECTS` with Google OAuth, or set a random `ADMIN_PASSWORD`
   (at least 16 characters). Generate each secret independently, for example:
   `python -c "import secrets; print(secrets.token_hex(32))"`.
3. Set `DATABASE_URL` to the PostgreSQL connection string using the matching
   database password. URL-encode reserved characters in the password.
4. Configure the actual reverse proxy with HTTPS. The app port is bound to
   **127.0.0.1:8080**, and PostgreSQL is not published. Set `FORWARDED_ALLOW_IPS` to
   the exact trusted proxy IPs/CIDRs as seen by Uvicorn. Do not use `*`.
5. Back up the existing PostgreSQL database before applying migrations.
6. Start through Compose with the env file available for variable interpolation:

```powershell
docker compose --env-file C:/Deploy/OptikaTimka/prod.env -f docker-compose.prod.yml up -d --build
```

`ENVIRONMENT=production` refuses weak/missing application secrets and a missing HTTPS
origin. The container runs without root privileges,
with a read-only filesystem and dropped capabilities. Runtime dependencies are
pinned and hash-verified in `requirements.lock`.

**Existing database volumes:** changing `POSTGRES_PASSWORD` in Compose does not
change the password of an existing PostgreSQL role. Rotate the role password
through PostgreSQL administration and update the environment together. The old
hardcoded `optika_timka` password must not remain in use. Never delete the volume
to change credentials.

### Security migration 20261110_0004

The migration preserves appointment records and their displayed contact fields.
It adds indexed normalized contacts, verified-identity flags, shared rate-limit
storage, and the active-slot unique index. It clears unverified cross-provider
contact claims and invalidates existing customer sessions/codes, requiring a fresh
login. Back up before rollout; a downgrade cannot restore invalidated sessions.

If active appointments already share the same date/time, the migration stops before
changing the schema. Review duplicates and cancel or reschedule them deliberately;
the migration does not silently cancel clients. A preflight query is:

```sql
SELECT preferred_date, preferred_time, COUNT(*)
FROM appointment
WHERE status <> 'canceled'
  AND preferred_date IS NOT NULL AND preferred_time IS NOT NULL
GROUP BY preferred_date, preferred_time HAVING COUNT(*) > 1;
```

### Booking duration migration 20261111_0005

This migration stores the duration for each existing booking. It keeps the
original start time and uses the longer of the old schedule block and the
current service duration. When the old block is missing, it uses 60 minutes
as a minimum. New bookings store the selected service's duration at booking time.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m bandit -r app -q
.\.venv\Scripts\python.exe -m pip_audit -r requirements.lock --no-deps --disable-pip
```

Tests use isolated SQLite databases. Set `TEST_DATABASE_URL` to a **dedicated test
PostgreSQL database** to run the same suite against PostgreSQL; the test role must
be able to create/drop schemas. Each test creates a randomly named schema and
removes only that schema afterward. Never point this setting at production.
Tests include migration upgrades/downgrades, identity isolation, CSRF failures,
authentication limits, and concurrent booking conflicts.

GitHub Actions runs SQLite and PostgreSQL tests on Python 3.12 and 3.14, Ruff,
Bandit, and the dependency audit. The workflow has no deployment steps.

To refresh the runtime lock deliberately:

```powershell
uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --upgrade -o requirements.lock
```

Reinstall locked dependencies and run the checks after an update.
