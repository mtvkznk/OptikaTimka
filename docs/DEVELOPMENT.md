# Робота з проєктом на іншому ПК

Цей сценарій переносить розробку. Чинний сервер, Telegram webhook та дані клієнтів
залишаються на сервері. Дата чи ім'я користувача в старому локальному шляху не
є вимогою проєкту. Команди нижче виконуються в PowerShell із кореня репозиторію.

## Отримання коду й середовища

Потрібні Git і Python 3.12+. Для локального PostgreSQL додатково потрібен Docker;
для окремого SQLite preview він не обов'язковий. Node потрібен лише для браузерних
тестів, не для запуску або збирання сайту.

```powershell
git clone https://github.com/mtvkznk/OptikaTimka.git C:/Projects/OptikaTimka
Set-Location C:/Projects/OptikaTimka
python -m venv .venv
.\.venv\Scripts\python.exe -B -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -B -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -B -m pip check
Copy-Item .env.example .env
```

Відредагувати локальний `.env`: задати власні `AUTH_SECRET`, `ADMIN_PASSWORD` і
локальну базу. Залишити `ENVIRONMENT=local`. Значення секрету можна згенерувати
локально через `python -c "import secrets; print(secrets.token_hex(32))"` і
зберегти без публікації. Для звичайного preview Google і Telegram вимкнені.

Стару `.venv` не копіювати: вона прив'язана до встановлення Python і шляхів ПК.
Незакомічені зміни перенести окремо після перевірки `git status`; GitHub їх не має.
Не копіювати production-конфігурацію до локального `.env` і не підставляти робочу
базу в `DATABASE_URL` чи `TEST_DATABASE_URL`.

Нова модель має почати з [../AGENTS.md](../AGENTS.md) та
[PROJECT_STATE.md](PROJECT_STATE.md). Колишня робоча папка чату, її `outputs` і
тимчасові release-скрипти не потрібні для наведеного тут запуску.

## Ізольований SQLite preview

У цьому PowerShell-сеансі явно вимкнути зовнішні інтеграції й задати окрему базу:

```powershell
$optikaScratch = Join-Path ([IO.Path]::GetTempPath()) 'optika-local-preview'
New-Item -ItemType Directory -Path $optikaScratch -Force | Out-Null
$optikaDatabase = (Join-Path $optikaScratch 'preview.sqlite').Replace('\', '/')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:ENVIRONMENT = 'local'
$env:DATABASE_URL = 'sqlite:///' + $optikaDatabase
$env:PUBLIC_BASE_URL = 'http://127.0.0.1:8000'
$env:GOOGLE_CLIENT_ID = ''
$env:GOOGLE_CLIENT_SECRET = ''
$env:GOOGLE_REDIRECT_URI = ''
$env:ADMIN_GOOGLE_EMAIL = ''
$env:ADMIN_GOOGLE_EMAILS = ''
$env:ADMIN_GOOGLE_SUBJECTS = ''
$env:TELEGRAM_BOT_TOKEN = ''
$env:TELEGRAM_BOT_USERNAME = ''
$env:TELEGRAM_WEBHOOK_SECRET = ''
.\.venv\Scripts\python.exe -B -c "from app.config import settings; assert settings.environment == 'local' and not settings.telegram_enabled and not settings.google_client_id and not settings.google_admin_configured, 'Clear external integration settings in the local .env before preview'"
if ($LASTEXITCODE -ne 0) { throw 'Preview configuration is not isolated' }
.\.venv\Scripts\python.exe -B -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Preview migration failed' }
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Відкрити `http://127.0.0.1:8000`; зупинка — Ctrl+C. Для адмінки використовується
локальний Basic-пароль із `.env`. Середовище не змінює робочого бота. Порожня база
отримає початкові послуги; їх назви/ціни можуть відрізнятися від живого сайту.
Закриття PowerShell прибирає задані тут процесні змінні оточення.
У Windows PowerShell 5.1 присвоєння порожньої змінної оточення може прибрати її,
після чого значення знову береться з `.env`. Тому перевірка конфігурації перед
запуском обов'язкова; зовнішні інтеграції мають бути порожні й у локальному файлі.

## Локальний PostgreSQL та Google

`docker-compose.yml` запускає тільки локальний PostgreSQL із тестовими
реквізитами та окремим volume. Із нової оболонки з локальним `.env`:

```powershell
docker compose up -d db
.\.venv\Scripts\python.exe -B -m alembic upgrade head
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

У `.env` має бути локальний PostgreSQL `DATABASE_URL` з `.env.example`. Якщо
використовується той самий PowerShell після SQLite preview, його процесні змінні
перекривають файл: відкрити новий сеанс або свідомо їх змінити.

Для реальної локальної перевірки Google потрібні OAuth-реквізити та дозволений
callback `http://127.0.0.1:8000/auth/google/callback`. Порт і hostname в браузері
та callback мають збігатися. Налаштування адміністратора описані в README.
Браузерна тестова fixture нижче не потребує справжнього Google входу.

## Автоматичні перевірки

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m ruff check --no-cache .
.\.venv\Scripts\python.exe -B -m bandit -r app -q
.\.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip
```

Pytest за замовчуванням створює ізольовані SQLite-бази. Для PostgreSQL задати
`TEST_DATABASE_URL` лише до **виділеної тестової бази**. Роль повинна мати право
створювати й видаляти схеми; кожний тест використовує власну випадкову схему.
Не запускати ці тести на робочій базі навіть із обіцянкою «лише прочитати».

Після зміни моделі/міграції додатково застосувати Alembic до свіжої тестової бази
та виконати `python -m alembic check`. Перевірка повинна пройти і на PostgreSQL.
SQLite не підтверджує коректність PostgreSQL locks, SQL-типів чи міграцій.

## Браузерні сценарії

CI задає точну версію Playwright у `.github/workflows/checks.yml`. Залежності
браузера можна встановити поза репозиторієм:

```powershell
$optikaBrowser = Join-Path ([IO.Path]::GetTempPath()) 'optika-playwright'
npm install --prefix $optikaBrowser --no-audit --no-fund playwright@1.65.0
node "$optikaBrowser/node_modules/playwright/cli.js" install chromium
$env:OPTIKA_PLAYWRIGHT = "$optikaBrowser/node_modules/playwright"
$env:PYTHONPATH = (Get-Location).ProviderPath
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B tests/browser/preview.py
```

Preview займає тільки `127.0.0.1:8017`, створює тимчасову SQLite-базу і тестового
адміністратора з фіксованим часом. Використовує фіктивні OAuth/Telegram значення,
не налаштовує реальний webhook; Telegram worker у `ENVIRONMENT=test` не запускається.

У другій оболонці з кореня репозиторію:

```powershell
$optikaBrowser = Join-Path ([IO.Path]::GetTempPath()) 'optika-playwright'
$env:OPTIKA_PLAYWRIGHT = "$optikaBrowser/node_modules/playwright"
node --test tests/browser/flows.cjs
```

Після завершення зупинити preview через Ctrl+C. Замість завантаженого Chromium
можна використати встановлений Edge, задавши `OPTIKA_BROWSER_CHANNEL=msedge`
у другій оболонці. Залежність Playwright усе одно потрібна.

## Коли яких перевірок достатньо

- Документація: перевірити посилання, шляхи, відповідність коду, відсутність секретів
  і синтаксис наведених команд. Деплой заради документів не потрібен.
- Інтерфейс: релевантні браузерні сценарії, мобільна ширина та відсутність JS-помилок.
- Бронювання/доступи/Telegram: тести відповідних правил і повний CI перед rollout.
- База: upgrade, downgrade з урахуванням втрат, `alembic check`, PostgreSQL CI.

GitHub Actions запускає Python 3.12/3.14, SQLite/PostgreSQL, браузерні сценарії,
Ruff, Bandit, аудит runtime-залежностей і production-container smoke. Push
запускає ці перевірки, але не оновлює сайт.

Для доступу до чинного сервера з нового ПК окремо потрібні Tailscale, SSH-ключ,
перевірений host key і дозволи GitHub для push. Отримати їх через захищений канал;
приватні ключі не є частиною репозиторію. Сервер переносити для цього не потрібно.
