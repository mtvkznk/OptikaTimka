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

### Telegram sign-in and administrator bot

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` (without `@`), and an independent
random `TELEGRAM_WEBHOOK_SECRET` (32–256 URL-safe characters) in the protected
production env file. Apply migration `20261121_0016`, restart the app, then run
`docker exec optika_timka_prod_app python -m app.telegram_setup`. This verifies the
bot identity and installs its commands and HTTPS webhook, preserving pending updates.
An existing unrelated webhook is never overwritten. Telegram uses the same app and
PostgreSQL database; no additional process or public database port is required.

In the cabinet, choose **Прив’язати Telegram**, open the bot, approve, return to the
same browser and confirm the link. Administrators first sign in with an authorized
Google account and link their own Telegram. The bot checks the current Google
administrator grants before every action. Public Telegram sign-in creates an
ordinary customer account; Telegram names/usernames never establish email, phone,
or administrator ownership. Linking can merge bookings from a Telegram-only
account after both identities are proved. A Telegram already linked to a different
Google account cannot be reassigned this way.

**Продовжити з Telegram** uses the same browser-bound approval flow, with expiring,
single-use secrets and session-bound CSRF protection. It needs no Telegram OIDC
client secret or external login scripts. Disconnecting Telegram in a Google-backed
cabinet revokes its website sessions and bot access immediately. Keep Google as
the recovery method for administrator accounts.

The private bot supports the doctor's day schedule, date navigation, manual
appointments with either name or contact, statuses, moving by selecting a free
hour, service-only hour closures, reopening, and all appointments with search and
pagination. Future removals require confirmation; past removals do not. Its
commands reuse the website's transaction lock and appointment validation. Future
manual bookings start as new; today's start confirmed. The timetable uses the
service's duration and weekday schedule and skips closed days. Schedule/catalog
editing remains in the website dashboard.

The current day timetable refreshes automatically, including changes from the
website and other administrators. The app checks the database every five seconds
and edits the existing Telegram message only when its visible content changes.
Unchanged keyboards keep their callback secrets alive without repeated Telegram
edits. The refresh worker runs inside the app's lifespan and resumes saved views
after an app restart. No separate service or extra secret is required. Send
`/start` once after this feature is installed to register the current message.

Only the latest timetable is followed. Opening a patient, a form, search, or a
confirmation pauses tracking; returning to the timetable resumes it. Rescheduling
keeps the selected appointment and target date while available hours update. A
deleted Telegram message or revoked administrator grant stops tracking. A
per-chat lock prevents a background edit from overwriting a newer interactive
screen and does not hold the website's booking lock during network requests.
Telegram rate-limit delays are respected and background edits never create a new
message when the original message has been deleted.

Webhook authentication is separate from browser CSRF and uses a constant-time
secret-header comparison. Update IDs prevent duplicate mutations; a transactional
outbox retries interrupted replies. Callback buttons expire and are bound to the
Telegram user; appointment versions reject stale actions. Undelivered admin
messages are discarded after access is revoked or after ten minutes (Telegram
rate-limit delays are honored before retry/expiry). Successful
replies remain in Telegram chat history, so use private chats on trusted devices.

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
Set `ADMIN_GOOGLE_EMAILS` to a comma-separated list of additional verified Google
emails to grant access on their first Google sign-in.
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
  use `Cache-Control: no-store`. Browsers can retain public catalog photos but
  must revalidate them before reuse. CSP, frame restrictions, and other security
  headers apply to responses. Production OpenAPI/Swagger/ReDoc endpoints are disabled.
- Public closed-date responses omit internal administrator notes.
- Production application access logs omit URL query strings.
- Request bodies are limited to 64 KiB. The administrator's batch save accepts up
  to 2 MiB so it can include product photos. Photos are resized in the browser,
  validated and re-encoded on the server, and stored with the catalog in PostgreSQL.
  Persistent database rate limits work across application processes. Raw IP
  addresses are not stored in rate-limit keys.
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

Set `DOCTOR_DIARY_SERVICE_ID` to the ID of the doctor's examination service
from `/api/booking-services` to keep the diary bound to that service after a rename.

On the Windows host, the `Optika Timka Recovery` scheduled task runs
`ops/windows/optika-recovery.ps1` every minute under the signed-in Docker
Desktop account. Keep the installed copy in `C:/Program Files/OptikaTimkaRecovery`.
The task can invoke the existing `Start Docker Desktop` task and recover the
existing production containers. Recovery actions appear in the Windows Application
event log under `Optika Timka Recovery`. Disable the recovery task during intentional
Docker or site maintenance and re-enable it afterward. Docker Desktop recovery
requires its Windows account to be signed in.

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
Bandit, and the dependency audit. It also runs the Playwright scenarios in
`tests/browser` against a temporary, seeded SQLite database. The workflow has no deployment steps.

To refresh the runtime lock deliberately:

```powershell
uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --upgrade -o requirements.lock
```

Reinstall locked dependencies and run the checks after an update.
