# Обслуговування чинного сервера

Поточні спостереження датовані в [PROJECT_STATE.md](PROJECT_STATE.md).
Після кожного коміту відправити його в GitHub і доставити перевірений архів
цього коміту на сервер. Для змін застосунку чи інфраструктури виконати rollout
нижче. Для документаційних правок опублікувати вихідний код у `releases/<commit>`
та звірити поточний runtime. `current-release.txt` фіксує активну версію
застосунку. Доступ і обсяг змін визначає актуальне доручення користувача.

## Карта розміщення

| Об'єкт | Поточна домовленість |
| --- | --- |
| Публічний сайт | `https://optykatimka.com.ua` |
| Бот | `@OptykaTimkaBot` |
| Доступ оператора | SSH через Tailscale; ціль/користувач у приватному профілі оператора |
| Корінь deployment на Windows | `C:/Deploy/OptikaTimka` |
| Захищена конфігурація | `C:/Deploy/OptikaTimka/prod.env`, поза checkout |
| Версії коду | `releases/<commit>/` |
| Передані архіви/скрипти | `incoming/` |
| Маркер успішного rollout | `current-release.txt` |
| Резервні копії | `backups/`, доступ обмежений ACL |
| Compose project | `site` — зберігати між версіями |
| Застосунок / PostgreSQL | `optika_timka_prod_app` / `optika_timka_prod_db` |
| Образи | `optika_timka_app:<commit>`, робочий alias `:latest` |
| Диск PostgreSQL | Існуючий Compose volume `optika_timka_prod_postgres_data` з префіксом проєкту; перевірити фактичне ім'я через inspect |
| Origin | Застосунок на `127.0.0.1:8080`, PostgreSQL без публічного порту |
| Зовнішній маршрут | Cloudflare; на Windows працює служба `Cloudflared` |
| Відновлення | Scheduled task `Optika Timka Recovery` |

Локальний каталог розробника не визначає ці шляхи. Клонування на іншому ПК не
переносить сервер, базу чи конфігурацію. Приватний профіль нового оператора має
містити SSH target, доступ Tailscale, ключ і перевірений відбиток host key.
Ціль можна передавати як `OPTIKA_SSH_TARGET` без запису реквізитів у Git.

## Перевірка стану без змін

На сервері, PowerShell:

```powershell
$optikaRoot = 'C:/Deploy/OptikaTimka'
Get-Content -LiteralPath "$optikaRoot/current-release.txt"
docker inspect --format '{{.State.Health.Status}}' optika_timka_prod_app
docker inspect --format '{{.State.Health.Status}}' optika_timka_prod_db
docker exec optika_timka_prod_app python -B -m alembic current
Get-Service -Name Cloudflared
Get-ScheduledTask -TaskName 'Optika Timka Recovery'
curl.exe --fail --silent --show-error --max-time 10 http://127.0.0.1:8080/health
curl.exe --fail --silent --show-error --max-time 10 https://optykatimka.com.ua/health
```

Маркер версії не доводить, що контейнер відповідає йому. Звірити поточний образ,
Compose labels, міграцію і HTTP health. Для labels без виведення env:

```powershell
$optikaLabels = (docker inspect --format '{{json .Config.Labels}}' optika_timka_prod_app) | ConvertFrom-Json
$optikaLabels.'com.docker.compose.project'
$optikaLabels.'com.docker.compose.project.config_files'
```

Не виводити `Config.Env`, вміст `prod.env`, приватні ключі або дампи таблиць у
чат/логи. Для помилок використовувати обмежений за часом `docker logs --since 10m`;
перед публікацією прибрати персональні дані та секрети. Windows PowerShell може
трактувати звичайний stderr Docker як помилку; перевіряти код завершення, не
лише наявність stderr. Не друкувати сирі винятки Telegram HTTP із URL токена.

## Конфігурація

`prod.env.example` показує назви змінних, але не копіює робочі значення.
Важливі `PUBLIC_BASE_URL`, `DATABASE_URL`, `POSTGRES_PASSWORD`, `AUTH_SECRET`,
Google OAuth, усі три Google admin grants, `DOCTOR_DIARY_SERVICE_ID`, Telegram
token/username/webhook secret, `FORWARDED_ALLOW_IPS`.

Зберігати наявні grants і секрети під час звичайного оновлення. Зміна
`AUTH_SECRET` інвалідує похідні токени; це не звичайна дія деплою. Зміна env-пароля
PostgreSQL не змінює пароль уже створеної DB-ролі: ротація потребує узгодженої
зміни ролі та конфігурації. Не перевстановлювати базу задля пароля.

Compose має отримати **і** файл для підстановки `${...}`, **і** env застосунку:

```powershell
$env:OPTIKA_ENV_FILE = 'C:/Deploy/OptikaTimka/prod.env'
docker compose --project-name site --env-file $env:OPTIKA_ENV_FILE -f C:/Deploy/OptikaTimka/releases/RELEASE/docker-compose.prod.yml config --quiet
```

Замінити `RELEASE` на перевірену версію. Використовувати `config --quiet`, щоб не
розкрити підставлені секрети. `docker run --env-file` і Compose по-різному
обробляють лапки у файлі: не підміняти Compose сирим `docker run` із `prod.env`.

## Резервна копія перед rollout

1. Створити окрему директорію в `backups/` з датою/версією.
2. Обмежити її ACL поточним оператором, SYSTEM і Administrators.
3. Виконати `pg_dump -Fc` всередині чинного DB-контейнера. `pg_dump` дає узгоджений
   snapshot; зупиняти чи видаляти PostgreSQL не потрібно.
4. Перевірити `pg_restore --list`, скопіювати архів, записати SHA-256, перевірити
   коди завершення та розмір. Це ще не перевірка повного відновлення.
5. Захищено зберегти відповідну конфігурацію поза Git. Дамп може містити пацієнтів
   і дані прив'язок; його не можна додавати до репозиторію чи публічного artifact.

Приклад архівування; виконувати на сервері після перевірки цілі:

```powershell
$optikaStamp = [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
$optikaBackup = "C:/Deploy/OptikaTimka/backups/manual-$optikaStamp"
New-Item -ItemType Directory -Path $optikaBackup | Out-Null
$optikaSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls.exe $optikaBackup /inheritance:r /grant:r ("*$optikaSid" + ':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Backup ACL failed' }
$optikaDump = "/tmp/optika-$optikaStamp.dump"
try {
    docker exec optika_timka_prod_db pg_dump --no-password -Fc -U optika_timka -d optika_timka -f $optikaDump
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }
    docker exec optika_timka_prod_db pg_restore --list $optikaDump | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Archive validation failed' }
    docker cp ("optika_timka_prod_db:" + $optikaDump) "$optikaBackup/postgres.dump"
    if ($LASTEXITCODE -ne 0) { throw 'Backup copy failed' }
    if ((Get-Item -LiteralPath "$optikaBackup/postgres.dump").Length -lt 1024) { throw 'Backup too small' }
    (Get-FileHash -LiteralPath "$optikaBackup/postgres.dump" -Algorithm SHA256).Hash | Set-Content -LiteralPath "$optikaBackup/postgres.sha256"
} finally {
    docker exec optika_timka_prod_db rm -f -- $optikaDump
}
```

Окремо перевіряти відновлення на виділеному тестовому PostgreSQL 16: нова порожня
база, без production-volume і публічного порту. Скопіювати dump, виконати
`pg_restore --exit-on-error --no-owner --no-acl` у тестову базу, звірити схему,
кількість сутностей і тестове читання застосунком тієї самої версії. У тесті
вимкнути справжні Google/Telegram credentials; зовнішні дії не виконувати.
Результат/дату записати в приватний операційний журнал. Періодичність, захищена
зовнішня копія та retention потребують окремо підтвердженого налаштування;
наявність deployment-backup не доводить наявності регулярних копій.

## Порядок деплою

1. Зафіксувати поточні Git commit, образ контейнера, schema revision, Compose
   project, volume та стан recovery task. Перевірити незакомічені зміни.
2. Виконати релевантні перевірки; для зміни поведінки/бази дочекатися зеленого CI
   саме потрібного commit. Не брати випадковий вміст робочої папки.
3. Створити `git archive` цього commit, записати SHA-256, передати через
   перевірений SSH/Tailscale. На сервері звірити хеш і розпакувати в новий
   `releases/<commit>`; попередній release залишити для відкату.
4. Зібрати `optika_timka_app:<commit>`, перевірити Compose і новий образ на
   ізольованій тестовій базі. До cutover не перетегувати `:latest` і не запускати
   новий app проти production-бази задля smoke.
5. Зробити перевірену резервну копію. Прочитати upgrade/downgrade міграції:
   сумісність старого застосунку не можна припускати.
6. Зберегти старий image ID під rollback-тегом. На час cutover вимкнути task
   `Optika Timka Recovery`, запам'ятавши попередній стан. Повернути стан у `finally`.
7. Перетегувати перевірений кандидат у `optika_timka_app:latest`. З того самого
   Compose project `site` запустити **лише app** з `--no-deps --no-build --wait`.
   Dockerfile запускає `alembic upgrade head` перед Uvicorn, тож міграція відбудеться
   під час старту. Не запускати паралельно старий і новий app із фоновими workers.
8. Перевірити health, очікувану schema revision/image, той самий DB-контейнер і
   volume, незмінність protected config, публічні сторінки та доступи. Не створювати
   тестових пацієнтів у production. Переглянути санітизовані нові помилки.
9. Записати `current-release.txt` **лише після успішної перевірки**, відновити
   recovery task, оновити PROJECT_STATE й передати результат користувачу.

Серверна команда кроку 7 після підготовки та backup:

```powershell
docker compose --project-name site --env-file C:/Deploy/OptikaTimka/prod.env -f C:/Deploy/OptikaTimka/releases/RELEASE/docker-compose.prod.yml up -d --no-deps --no-build --wait --wait-timeout 75 app
```

Не використовувати `down -v`, `volume rm`, нове ім'я Compose project або seed-reset
для звичайного rollout. Вони можуть відкрити порожню базу або знищити дані.
Старі одноразові build/cutover скрипти з папки конкретного чату — історичні
матеріали, не універсальні засоби деплою. Перед повторним запуском їх треба
переглянути: вони прив'язані до commit, schema revision і контрольних сум.

## Відкат

Зупинити новий app, щоб він не писав під час відкату. Якщо схема сумісна зі
старим кодом, повернути попередній image через той самий Compose project та
перевірити health. Якщо потрібен downgrade, виконати його **образом із новою
міграцією**, доки цей образ доступний, і лише після перегляду втрат/обмежень.
Compose `run --rm --no-deps -T app alembic downgrade REVISION` запускає Alembic
замість звичайного CMD; перевірити, який image на цей момент має alias `latest`.

Не підміняти downgrade відновленням старого dump автоматично: записи після
snapshot можуть загубитися. Відновлення робочої бази потребує окремого плану з
актуальною копією пошкодженого стану, узгодженим вікном зупинки, перевіреним
dump та відповідною версією коду. Простіший шлях — сумісний application rollback
або виправлення міграції вперед.

При поверненні з `20261122_0019` до `0018` таблиця старого login-flow відтворюється
порожньою; відкликані Telegram website sessions не повертаються. Це очікувано.
Після будь-якого відкату перевірити схему, HTTP, дані й recovery task, а маркер
версії встановити відповідно до фактично запущеного застосунку.

## Якщо Cloudflare повертає 502

Перевіряти послідовно, не перезавантажуючи все навмання:

1. Tailscale/SSH доступ і стан самого Windows host.
2. Docker Desktop engine та `optika_timka_prod_db` health.
3. `optika_timka_prod_app`: health, останні повідомлення старту/міграції.
4. `http://127.0.0.1:8080/health` на сервері.
5. Служба `Cloudflared`, її приватна tunnel-конфігурація та public `/health`.

Здоровий origin із несправним public маршрутом вказує на перевірку tunnel/proxy.
Нездоровий origin — на застосунок або базу. Сам код 502 не доводить конкретну причину.

Recovery task раз на хвилину запускає встановлену копію
`C:/Program Files/OptikaTimkaRecovery/optika-recovery.ps1` (джерело в репозиторії).
Він може запустити існуючий DB/app, запросити `Start Docker Desktop` або
перезапустити app, який unhealthy понад п'ять хвилин. Не створює нової бази й
не лікує будь-яку помилку мережі/Cloudflare. Docker Desktop потребує входу свого
Windows-користувача. Події — Windows Application log, source `Optika Timka Recovery`.

## Telegram після оновлення

Перезапуск із тим самим доменом/ботом не потребує повторної прив'язки адмінів.
За зміни webhook/початкового підключення виконати явно:

```powershell
docker exec optika_timka_prod_app python -B -m app.telegram_setup
```

Команда перевіряє ім'я бота, не перезаписує чужий webhook і зберігає pending updates.
Не запускати її з локального preview для робочого бота. Налаштування токенів не
слід «перевіряти» надсиланням тестових повідомлень пацієнтам чи адміністраторам
без відповідного доручення.
