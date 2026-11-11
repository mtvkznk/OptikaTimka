import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select
from starlette.datastructures import URL, MutableHeaders
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.database import get_session
from app.models import Customer, CustomerSession, SecurityRateLimit, utc_now

SESSION_COOKIE_NAME = "optika_customer_session"
GOOGLE_SESSION_PREFIX = "g."
CSRF_COOKIE_NAME = "optika_csrf"
MAX_BODY_BYTES = 65_536
CSRF_TTL_SECONDS = 24 * 60 * 60
basic_auth = HTTPBasic(auto_error=False, realm="Optika Timka admin")


def production() -> bool:
    return settings.environment == "production"


def cookie_name(name: str) -> str:
    return f"__Host-{name}" if production() else name


def hash_secret(value: str) -> str:
    return hmac.new(
        settings.auth_secret.get_secret_value().encode(),
        value.encode(),
        hashlib.sha256,
    ).hexdigest()


def hash_session_token(token: str) -> str:
    return hash_secret(f"customer-session:{token}")


def utc_expired(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value < utc_now()


def get_customer_by_session(request: Request, session: Session) -> Customer | None:
    token = request.cookies.get(cookie_name(SESSION_COOKIE_NAME), "")
    if not token or len(token) > 128:
        return None
    customer_session = session.exec(
        select(CustomerSession).where(CustomerSession.token_hash == hash_session_token(token))
    ).first()
    if customer_session is None or utc_expired(customer_session.expires_at):
        return None
    return session.get(Customer, customer_session.customer_id)


def google_session(request: Request) -> bool:
    # The prefix is part of the hashed session token, so adding it cannot elevate a phone session.
    return request.cookies.get(cookie_name(SESSION_COOKIE_NAME), "").startswith(GOOGLE_SESSION_PREFIX)


def is_google_admin(request: Request, customer: Customer | None) -> bool:
    return bool(
        customer
        and customer.google_sub
        and customer.email_verified
        and google_session(request)
        and (
            customer.google_sub in settings.admin_google_subjects
            or (settings.admin_google_email and customer.email == settings.admin_google_email)
        )
    )


def csrf_signature(payload: str, session_token: str) -> str:
    return hash_secret(f"csrf:{session_token}:{payload}")


def valid_csrf_token(token: str, session_token: str) -> bool:
    if not token or len(token) > 160:
        return False
    try:
        timestamp, nonce, signature = token.split(".")
        age = int(datetime.now(UTC).timestamp()) - int(timestamp)
    except (ValueError, TypeError):
        return False
    return (
        0 <= age <= CSRF_TTL_SECONDS
        and len(nonce) == 32
        and secrets.compare_digest(
            signature.encode(),
            csrf_signature(f"{timestamp}.{nonce}", session_token).encode(),
        )
    )


def parse_form_body(body: bytes, content_type: str) -> dict[str, str]:
    if content_type.split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Форма має бути надіслана у стандартному форматі.")
    try:
        parsed = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=64,
            errors="strict",
        )
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(400, "Некоректні дані форми.") from exc
    if any(len(values) != 1 for values in parsed.values()):
        raise HTTPException(400, "Поля форми не повинні повторюватися.")
    return {key: values[0] for key, values in parsed.items()}


async def read_form_data(request: Request) -> dict[str, str]:
    return parse_form_body(await request.body(), request.headers.get("content-type", ""))


class SecurityMiddleware:
    """Bound request bodies and enforce signed, session-bound CSRF tokens."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        session_token = request.cookies.get(cookie_name(SESSION_COOKIE_NAME), "")
        cookie_token = request.cookies.get(cookie_name(CSRF_COOKIE_NAME), "")
        token_valid = valid_csrf_token(cookie_token, session_token)
        if token_valid:
            csrf_token = cookie_token
        else:
            payload = f"{int(datetime.now(UTC).timestamp())}.{secrets.token_hex(16)}"
            csrf_token = f"{payload}.{csrf_signature(payload, session_token)}"
        scope.setdefault("state", {})["csrf_token"] = csrf_token

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                # Native form POSTs need their Origin header. Keep OAuth redirect URLs private.
                headers["Referrer-Policy"] = (
                    "no-referrer" if scope["path"].startswith("/auth/google") else "same-origin"
                )
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                )
                if not scope["path"].startswith("/static/"):
                    headers["Cache-Control"] = "no-store"
                if production():
                    headers["Strict-Transport-Security"] = "max-age=31536000"
                if not token_valid and "text/html" in headers.get("content-type", ""):
                    cookie_response = JSONResponse({})
                    cookie_response.set_cookie(
                        cookie_name(CSRF_COOKIE_NAME),
                        csrf_token,
                        max_age=CSRF_TTL_SECONDS,
                        httponly=True,
                        secure=production(),
                        samesite="strict",
                        path="/",
                    )
                    headers.append("Set-Cookie", cookie_response.headers["set-cookie"])
            await send(message)

        async def reject(status_code: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code)(scope, receive, send_with_headers)

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                content_length = int(request.headers.get("content-length", "0"))
            except ValueError:
                await reject(400, "Некоректна довжина запиту.")
                return
            if content_length < 0 or content_length > MAX_BODY_BYTES:
                await reject(413, "Запит завеликий.")
                return
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_BODY_BYTES:
                    await reject(413, "Запит завеликий.")
                    return
                if not message.get("more_body", False):
                    break

            expected_origin = settings.public_base_url or str(request.base_url).rstrip("/")
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            if not origin and referer:
                try:
                    parsed = urlsplit(referer)
                except ValueError:
                    await reject(403, "Некоректне джерело запиту.")
                    return
                origin = f"{parsed.scheme}://{parsed.netloc}"
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and origin.rstrip("/") != expected_origin
            ):
                await reject(403, "Запит з іншого сайту заборонено.")
                return
            submitted = request.headers.get("x-csrf-token", "")
            if not submitted and request.headers.get("content-type", "").startswith(
                "application/x-www-form-urlencoded"
            ):
                try:
                    submitted = parse_form_body(bytes(body), request.headers["content-type"]).get(
                        "csrf_token",
                        "",
                    )
                except HTTPException as exc:
                    await reject(exc.status_code, str(exc.detail))
                    return
            if not token_valid or not secrets.compare_digest(
                submitted.encode(), cookie_token.encode()
            ):
                await reject(403, "Оновіть сторінку та повторіть дію.")
                return

            sent = False

            async def receive_body() -> Message:
                nonlocal sent
                if sent:
                    return await receive()
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            await self.app(scope, receive_body, send_with_headers)
        else:
            await self.app(scope, receive, send_with_headers)


class CanonicalHostMiddleware:
    """Keep browser sessions and Google OAuth on the configured production host."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in {"GET", "HEAD"}
            and production()
            and settings.public_base_url
        ):
            origin = urlsplit(settings.public_base_url)
            host = origin.netloc.lower()
            alias = host.removeprefix("www.") if host.startswith("www.") else f"www.{host}"
            request = Request(scope)
            if request.headers.get("host", "").lower() == alias:
                url = URL(scope=scope).replace(scheme=origin.scheme, netloc=origin.netloc)
                await RedirectResponse(url, status_code=308)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def rate_limit(
    session: Session,
    request: Request,
    action: str,
    limit: int,
    seconds: int,
    *,
    identifier: str | None = None,
) -> None:
    """Atomic database buckets work across processes; raw IPs are never stored."""
    now = utc_now()
    window = int(now.timestamp()) // seconds
    identity = identifier or (request.client.host if request.client else "unknown")
    key = hash_secret(f"rate-limit:{action}:{identity}:{window}")
    table = SecurityRateLimit.__table__
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    statement = (
        insert(table)
        .values(
            key=key,
            hits=1,
            expires_at=datetime.fromtimestamp((window + 1) * seconds, UTC),
        )
        .on_conflict_do_update(
            index_elements=[table.c.key],
            set_={"hits": table.c.hits + 1},
            where=table.c.hits < limit,
        )
        .returning(table.c.key)
    )
    allowed = session.execute(statement).scalar_one_or_none() is not None
    session.execute(delete(SecurityRateLimit).where(SecurityRateLimit.expires_at < now))
    session.commit()
    if not allowed:
        retry_after = max(1, (window + 1) * seconds - int(now.timestamp()))
        raise HTTPException(
            429,
            "Забагато запитів. Спробуйте трохи пізніше.",
            headers={"Retry-After": str(retry_after)},
        )


def require_admin(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)],
) -> None:
    if settings.google_admin_configured:
        customer = get_customer_by_session(request, session) if google_session(request) else None
        if is_google_admin(request, customer):
            return
        raise HTTPException(403, "Увійдіть у кабінет через Google-акаунт адміністратора.")
    if not settings.admin_password:
        raise HTTPException(503, "Доступ адміністратора тимчасово недоступний.")
    if credentials is not None:
        username_ok = secrets.compare_digest(
            credentials.username.encode(),
            settings.admin_username.encode(),
        )
        password_ok = secrets.compare_digest(
            credentials.password.encode(),
            settings.admin_password.get_secret_value().encode(),
        )
        if username_ok and password_ok:
            return
    rate_limit(session, request, "admin-login", 10, 300)
    raise HTTPException(
        401,
        "Потрібен вхід адміністратора.",
        headers={"WWW-Authenticate": 'Basic realm="Optika Timka admin", charset="UTF-8"'},
    )
