# Working on Optika Timka

This repository contains the website AND its administrator Telegram bot. Keep
important decisions here; chat history and a previous agent's memory are not
required project dependencies.

## Start here

1. Read [README.md](README.md) and [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md).
2. For behavior changes, read [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md).
3. Use [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) to find the owning modules.
4. Read [docs/OPERATIONS.md](docs/OPERATIONS.md) before any production work.
5. Use [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) on a new computer.

Treat PROJECT_STATE as a dated snapshot, not proof of the server's present state.
Inspect the current branch, diff, migrations and relevant code before editing.
If code and documentation disagree, report the discrepancy and resolve it using
the user's current instructions. Do not silently change a business rule.

## Environment and workflow

- Work from this repository's actual root; no particular Windows username,
  dated parent directory or drive letter is required.
- Python 3.12+; reuse `.venv/Scripts/python.exe` on Windows or `.venv/bin/python`
  on other systems. Recreate the virtual environment on a new computer.
- FastAPI, SQLModel, Alembic, PostgreSQL, Jinja2, plain CSS and JavaScript.
  There is no application Node build. Playwright/Node is only for browser tests.
- Use a separate SQLite preview or dedicated test PostgreSQL database. Never
  point tests or a local preview at production. Disable the production Telegram
  credentials locally; importing the app can start its refresh worker.
- Use `rg` for search. Keep scratch files outside tracked source; avoid committing
  local caches, database files, screenshots, logs and release ZIPs.
- Follow the user's current authorization for commits, pushing and deployment.
  Deployment is a separate operation from a Git push; CI does not deploy.

## Invariants to preserve

- Website sign-in is Google only. No phone or Telegram website login.
- Telegram linking requires a live authorized Google administrator session;
  confirm once in the bot. The bot never issues website sessions.
- The website and bot share ONE PostgreSQL database and transactional commands
  in `app/diary_commands.py`. Do not build a second booking store or duplicate
  mutation/availability logic in Telegram.
- The notebook belongs only to the doctor examination service, resolved by
  `DOCTOR_DIARY_SERVICE_ID` in production. Preserve service IDs and patient data.
- Active appointments cannot overlap across services. Blocking an hour is
  service-specific. These are deliberately different scopes.
- Preserve the existing booking duration on a move. Future manual appointments
  start `new`; today's manual appointments start `confirmed` (Kyiv time).
- Use `Europe/Kyiv` for business dates; persisted audit/session timestamps use UTC.
- Shared appointment mutations must retain the database booking lock and stale
  version checks. The controller owns commit/rollback. Do not add commits inside
  shared commands or hold the booking transaction lock during Telegram HTTP calls.
- Schedule/catalog edits are a reviewed draft with one save. Direct diary actions
  (create, move, block/reopen, remove) apply immediately. Do not turn patient
  deletion into an unsaved draft or remove confirmation for future deletion.
- Keep the visible brand `Оптика Тімка`, shared templates and mobile layouts.
  Refresh the appropriate asset version when changing shipped CSS/JavaScript.

## Secrets, data and operations

- Never commit or print `.env`/`prod.env`, database credentials, OAuth secrets,
  bot tokens, webhook secrets, SSH private keys or patient data. Environment
  examples contain placeholders only; production grants live in protected config.
- Do not log Telegram request URLs: the bot token is part of the URL.
- No production volume deletion, seed resets or test-patient cleanup as a routine
  fix. Take a verified database backup before a migration or server rollout.
- Keep Compose project `site`, the existing production database volume and
  protected env file intact. Read the operations guide for maintenance/recovery
  coordination, health verification and rollback.
- Actual host login details and keys belong in a private operator profile outside
  Git. Missing access is not a reason to weaken SSH host-key verification.

## Verification and handoff

- Select checks appropriate to the change; see DEVELOPMENT for exact commands.
  Booking/auth/database changes require relevant tests and PostgreSQL CI, not
  just SQLite. UI changes require relevant browser scenarios.
- Documentation-only changes need link/path/command review and secret checks;
  they do not require a production redeploy.
- Explain what changed, what was verified and any remaining limitation.
- Update BUSINESS_RULES/DECISIONS when behavior changes; update ARCHITECTURE when
  ownership changes; update OPERATIONS when infrastructure changes. Record dated
  production verification and unresolved work in PROJECT_STATE after a rollout.
  Do not treat historical test counts or a clean tree statement as permanent facts.
