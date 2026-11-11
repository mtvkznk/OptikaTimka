import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app import main
from app.config import Settings, settings
from app.models import (
    Appointment,
    Customer,
    CustomerSession,
    PhoneAuthCode,
    SecurityRateLimit,
    utc_now,
)
from app.security import GOOGLE_SESSION_PREFIX, SESSION_COOKIE_NAME, cookie_name, hash_session_token


def booking(**changes: object) -> dict[str, object]:
    return {
        "name": "Test Client",
        "phone": "+380980000001",
        "email": None,
        "service": "Перевірка зору",
        "preferred_date": (main.current_date() + timedelta(days=2)).isoformat(),
        "preferred_time": "10:00:00",
        "message": "",
        **changes,
    }


def csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/cabinet")
    token = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert token
    return {"X-CSRF-Token": token.group(1)}


def phone_code(client: TestClient, phone: str = "+380980000001") -> str:
    response = client.post("/auth/phone/request", data={"phone": phone})
    assert response.status_code == 200
    match = re.search(r"Тестовий код:\s*<strong>([0-9]{6})</strong>", response.text)
    assert match
    return match.group(1)


def sign_in_customer(client: TestClient, engine: Engine, customer: Customer) -> None:
    token = "test-only-session-token-for-identity-checks"
    with Session(engine) as session:
        session.add(customer)
        session.flush()
        session.add(
            CustomerSession(
                customer_id=customer.id,
                token_hash=hash_session_token(token),
                expires_at=utc_now() + timedelta(hours=1),
            )
        )
        session.commit()
    client.cookies.set(cookie_name(SESSION_COOKIE_NAME), token)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/admin"),
        ("GET", "/admin/appointments"),
        ("POST", "/admin/changes"),
        ("POST", "/admin/services"),
        ("POST", "/admin/time-slots"),
        ("POST", "/admin/closed-dates"),
        ("POST", "/admin/appointments/1/status"),
        ("POST", "/admin/reorder/services"),
        ("POST", "/admin/reorder/time-slots"),
        ("POST", "/admin/services/1"),
        ("POST", "/admin/time-slots/1"),
        ("POST", "/admin/closed-dates/1"),
        ("POST", "/admin/closed-dates/1/delete"),
        ("POST", "/api/products"),
    ],
)
def test_admin_routes_require_authentication(
    anonymous_client: TestClient,
    method: str,
    path: str,
) -> None:
    response = anonymous_client.request(method, path, json={})
    assert response.status_code == 401
    assert "Basic" in response.headers["WWW-Authenticate"]


def test_admin_fails_closed_without_credentials(
    raw_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_password", None)
    assert raw_client.get("/admin").status_code == 503


def test_admin_password_guessing_is_limited(raw_client: TestClient) -> None:
    for _ in range(10):
        assert raw_client.get("/admin", auth=("admin", "wrong")).status_code == 401
    response = raw_client.get("/admin", auth=("admin", "wrong"))
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_mutation_requires_csrf(raw_client: TestClient) -> None:
    assert raw_client.post("/api/appointments", json=booking()).status_code == 403
    headers = csrf_headers(raw_client)
    assert (
        raw_client.post(
            "/api/appointments",
            json=booking(),
            headers={"X-CSRF-Token": "forged"},
        ).status_code
        == 403
    )
    assert raw_client.post("/api/appointments", json=booking(), headers=headers).status_code == 201


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://attacker.example"},
        {"Origin": "http://testserver.attacker.example"},
        {"Origin": "null"},
        {"Referer": "https://attacker.example/"},
        {"Referer": "http://["},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_site_mutation_rejected(raw_client: TestClient, headers: dict[str, str]) -> None:
    token_headers = csrf_headers(raw_client)
    response = raw_client.post("/api/appointments", json=booking(), headers=token_headers | headers)
    assert response.status_code == 403


def test_form_token_works_without_custom_header(raw_client: TestClient) -> None:
    token = csrf_headers(raw_client)["X-CSRF-Token"]
    response = raw_client.post(
        "/auth/phone/request",
        data={"phone": "+380980000001", "csrf_token": token},
    )
    assert response.status_code == 200


def test_csrf_is_bound_to_login_session(anonymous_client: TestClient) -> None:
    old_headers = csrf_headers(anonymous_client)
    code = phone_code(anonymous_client)
    response = anonymous_client.post(
        "/auth/phone/verify",
        data={"phone": "+380980000001", "code": code},
        follow_redirects=False,
    )
    assert response.status_code == 303
    # Bypass the browser fixture's automatic token refresh for this negative check.
    response = TestClient.request(
        anonymous_client,
        "POST",
        "/auth/logout",
        headers=old_headers,
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert anonymous_client.post("/auth/logout", follow_redirects=False).status_code == 303


def test_body_limits_cover_declared_and_streamed_bodies(raw_client: TestClient) -> None:
    headers = csrf_headers(raw_client)
    assert (
        raw_client.post(
            "/api/appointments",
            content=b"x",
            headers=headers | {"Content-Length": "999999"},
        ).status_code
        == 413
    )
    assert (
        raw_client.post(
            "/api/appointments",
            content=iter([b"x" * 40_000, b"x" * 40_000]),
            headers=headers,
        ).status_code
        == 413
    )


def test_host_and_security_headers(raw_client: TestClient) -> None:
    assert raw_client.get("/health", headers={"Host": "attacker.example"}).status_code == 400
    response = raw_client.get("/cabinet")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "HttpOnly" in response.headers["set-cookie"]
    oauth_redirect = raw_client.get("/auth/google", follow_redirects=False)
    assert oauth_redirect.headers["Referrer-Policy"] == "no-referrer"


def test_production_has_secure_host_cookies_and_no_phone_login(
    raw_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    response = raw_client.get("/cabinet")
    assert "__Host-optika_csrf=" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "Strict-Transport-Security" in response.headers
    assert "Тестовий код:" not in response.text
    assert 'action="/auth/phone/request"' not in response.text


def test_production_alias_redirects_before_creating_oauth_state(
    raw_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://optika.example")
    response = raw_client.get(
        "/auth/google?demo=1",
        headers={"Host": "www.optika.example"},
        follow_redirects=False,
    )
    assert response.status_code == 308
    assert response.headers["Location"] == "https://optika.example/auth/google?demo=1"
    assert "Set-Cookie" not in response.headers
    assert raw_client.get("/health", headers={"Host": "unrelated.example"}).status_code == 400


@pytest.mark.parametrize(
    "changes",
    [
        {"auth_secret": "short"},
        {"auth_secret": "replace-with-a-long-random-string"},
        {"admin_password": "replace-with-a-long-admin-password"},
        {"database_url": "postgresql+psycopg://optika_timka:optika_timka@db/optika_timka"},
        {"database_url": "sqlite://"},
        {"admin_password": "short"},
        {"public_base_url": "http://optika.example"},
        {"public_base_url": None},
        {"phone_auth_debug": True},
        {"environment": "prodction"},
        {"public_base_url": "https://optika.example/path"},
        {"admin_google_email": "owner@example.com"},
        {"admin_google_email": "owner@example.com,other@example.com"},
        {"admin_google_subjects": "test-google-sub"},
    ],
)
def test_unsafe_production_configuration_rejected(changes: dict[str, object]) -> None:
    options = {
        "environment": "production",
        "auth_secret": "test-only-secret-" * 4,
        "admin_password": "test-only-admin-password-123",
        "public_base_url": "https://optika.example",
        "phone_auth_debug": False,
        "database_url": "postgresql+psycopg://app:test-only-database-password@db/optika_timka",
        **changes,
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **options)


def test_valid_production_configuration_is_accepted() -> None:
    configured = Settings(
        _env_file=None, environment="production", auth_secret="test-only-secret-" * 4,
        admin_password="test-only-admin-password-123", public_base_url="https://optika.example",
        database_url="postgresql+psycopg://app:test-only-database-password@db/optika_timka",
        phone_auth_debug=False,
    )
    assert configured.environment == "production"


def test_production_google_admin_does_not_need_an_admin_password() -> None:
    configured = Settings(
        _env_file=None, environment="production", auth_secret="test-only-secret-" * 4,
        public_base_url="https://optika.example", google_client_id="test-client",
        google_client_secret="test-secret", admin_google_email=" Owner@Example.com ",
        database_url="postgresql+psycopg://app:test-only-database-password@db/optika_timka",
        admin_password=None,
    )
    assert configured.admin_google_email == "owner@example.com"
    assert configured.admin_password is None


def test_google_subjects_only_can_configure_admin_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_GOOGLE_SUBJECTS", " first-sub , second-sub ")
    configured = Settings(
        _env_file=None, environment="production", auth_secret="test-only-secret-" * 4,
        public_base_url="https://optika.example", google_client_id="test-client",
        google_client_secret="test-secret", admin_google_email=None,
        database_url="postgresql+psycopg://app:test-only-database-password@db/optika_timka",
        admin_password=None,
    )
    assert configured.admin_google_subjects == {"first-sub", "second-sub"}
    assert configured.google_admin_configured


def test_wrong_phone_codes_exhaust_attempts(anonymous_client: TestClient, app_db: Engine) -> None:
    code = phone_code(anonymous_client)
    wrong = "000000" if code != "000000" else "000001"
    for _ in range(main.PHONE_CODE_MAX_ATTEMPTS):
        assert (
            anonymous_client.post(
                "/auth/phone/verify",
                data={"phone": "+380980000001", "code": wrong},
            ).status_code
            == 400
        )
    assert (
        anonymous_client.post(
            "/auth/phone/verify",
            data={"phone": "+380980000001", "code": code},
        ).status_code
        == 400
    )
    with Session(app_db) as session:
        assert session.exec(select(PhoneAuthCode)).one().attempts == main.PHONE_CODE_MAX_ATTEMPTS
        assert session.exec(select(CustomerSession)).first() is None


def test_phone_code_request_cooldown_and_replay(anonymous_client: TestClient) -> None:
    code = phone_code(anonymous_client)
    assert (
        anonymous_client.post(
            "/auth/phone/request",
            data={"phone": "+380980000001"},
        ).status_code
        == 429
    )
    data = {"phone": "+380980000001", "code": code}
    assert (
        anonymous_client.post("/auth/phone/verify", data=data, follow_redirects=False).status_code
        == 303
    )
    anonymous_client.post("/auth/logout")
    assert anonymous_client.post("/auth/phone/verify", data=data).status_code == 400


def test_phone_login_requires_explicit_debug_flag(
    anonymous_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "phone_auth_debug", False)
    for path in ("request", "verify"):
        assert anonymous_client.post(
            f"/auth/phone/{path}", data={"phone": "+380980000001"},
        ).status_code == 503


def test_booking_contacts_cannot_claim_another_customers_records(
    anonymous_client: TestClient,
    app_db: Engine,
) -> None:
    assert (
        anonymous_client.post(
            "/api/appointments",
            json=booking(
                name="Victim Private Appointment",
                email="victim@example.com",
            ),
        ).status_code
        == 201
    )
    customer = Customer(
        google_sub="attacker-google-sub",
        email="attacker@example.com",
        email_verified=True,
    )
    sign_in_customer(anonymous_client, app_db, customer)
    assert (
        anonymous_client.post(
            "/api/appointments",
            json=booking(
                name="Attacker Own Appointment",
                email="victim@example.com",
                preferred_time="11:00:00",
            ),
        ).status_code
        == 201
    )
    cabinet = anonymous_client.get("/cabinet")
    assert "Victim Private Appointment" not in cabinet.text
    with Session(app_db) as session:
        stored = session.exec(select(Customer)).one()
        assert stored.phone is None
        assert stored.email == "attacker@example.com"
        assert len(main.customer_appointments(stored, session)) == 1


def test_verified_contact_cannot_read_records_owned_by_another_account(
    anonymous_client: TestClient,
    app_db: Engine,
) -> None:
    with Session(app_db) as session:
        victim = Customer(google_sub="victim")
        session.add(victim)
        session.flush()
        session.add(
            Appointment(
                name="Private Owned Record",
                phone="+380980000001",
                phone_normalized="+380980000001",
                service="Private service",
                customer_id=victim.id,
            )
        )
        session.commit()
    sign_in_customer(
        anonymous_client,
        app_db,
        Customer(phone="+380980000001", phone_verified=True),
    )
    assert "Private service" not in anonymous_client.get("/cabinet").text


@pytest.mark.parametrize(
    ("email", "google_sub", "is_admin"),
    [
        ("owner@example.com", "owner-sub", True),
        ("additional@example.net", "approved-sub", True),
        ("additional@example.net", "other-sub", False),
    ],
)
def test_google_login_opens_customer_cabinet(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch,
    email: str, google_sub: str, is_admin: bool,
) -> None:
    monkeypatch.setattr(settings, "google_client_id", "test-client")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-secret"))
    monkeypatch.setattr(settings, "admin_google_email", "owner@example.com")
    monkeypatch.setattr(settings, "admin_google_subjects", frozenset({"approved-sub"}))
    assert anonymous_client.get(
        "/admin", auth=(settings.admin_username, settings.admin_password.get_secret_value())
    ).status_code == 403
    start = anonymous_client.get("/auth/google", follow_redirects=False)
    assert start.status_code == 307
    authorization = urlsplit(start.headers["location"])
    assert authorization.hostname == "accounts.google.com"
    params = parse_qs(authorization.query)
    verifier = anonymous_client.cookies[cookie_name(main.OAUTH_VERIFIER_COOKIE_NAME)]

    def google_response(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://oauth2.googleapis.com/token"):
            assert request.method == "POST"
            data = parse_qs(request.content.decode())
            assert data["code"] == ["test-code"]
            assert data["code_verifier"] == [verifier]
            assert data["redirect_uri"] == params["redirect_uri"]
            return httpx.Response(200, json={"access_token": "test-access-token"})
        assert request.url == httpx.URL("https://openidconnect.googleapis.com/v1/userinfo")
        assert request.headers["Authorization"] == "Bearer test-access-token"
        return httpx.Response(
            200,
            json={
                "sub": google_sub,
                "email": email,
                "email_verified": True,
                "name": "Google Test Client",
            },
        )

    original_client = httpx.Client
    transport = httpx.MockTransport(google_response)
    monkeypatch.setattr(
        main.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs)
    )
    result = anonymous_client.get(
        "/auth/google/callback",
        params={"code": "test-code", "state": params["state"][0]},
        follow_redirects=True,
    )
    assert result.status_code == 200
    assert result.url.path == "/cabinet"
    assert "Google Test Client" in result.text
    assert cookie_name(SESSION_COOKIE_NAME) in anonymous_client.cookies
    assert cookie_name(main.OAUTH_STATE_COOKIE_NAME) not in anonymous_client.cookies
    assert cookie_name(main.OAUTH_VERIFIER_COOKIE_NAME) not in anonymous_client.cookies
    assert ('href="/admin"' in result.text) is is_admin
    assert anonymous_client.get("/admin").status_code == (200 if is_admin else 403)
    if not is_admin:
        assert anonymous_client.get("/admin/appointments").status_code == 403
        assert anonymous_client.post("/api/products", json={}).status_code == 403
    else:
        assert anonymous_client.post("/auth/logout").status_code == 200
        assert anonymous_client.get("/admin").status_code == 403


def test_google_admin_rejects_non_google_session_for_owner(
    anonymous_client: TestClient, app_db: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_google_email", "owner@example.com")
    sign_in_customer(
        anonymous_client, app_db,
        Customer(google_sub="owner", email="owner@example.com", email_verified=True),
    )
    assert 'href="/admin"' not in anonymous_client.get("/cabinet").text
    assert anonymous_client.get("/admin").status_code == 403
    name = cookie_name(SESSION_COOKIE_NAME)
    anonymous_client.cookies.set(name, GOOGLE_SESSION_PREFIX + anonymous_client.cookies[name])
    assert anonymous_client.get("/admin").status_code == 403


@pytest.mark.parametrize("verified", [False, "true", None])
def test_google_requires_verified_email(
    anonymous_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    verified: object,
) -> None:
    monkeypatch.setattr(settings, "google_client_id", "test-client")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-secret"))
    login = anonymous_client.get("/auth/google", follow_redirects=False)
    params = parse_qs(urlsplit(login.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]
    monkeypatch.setattr(
        main,
        "exchange_google_code",
        lambda *args: {
            "sub": "google-sub",
            "email": "victim@example.com",
            "email_verified": verified,
        },
    )
    response = anonymous_client.get(
        "/auth/google/callback",
        params={"code": "test-code", "state": params["state"][0]},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/cabinet?auth_error=google_failed"
    assert SESSION_COOKIE_NAME not in anonymous_client.cookies
    assert "optika_oauth_state" not in anonymous_client.cookies


def test_google_state_mismatch_never_exchanges_code(
    anonymous_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "google_client_id", "test-client")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-secret"))
    assert anonymous_client.get("/auth/google", follow_redirects=False).status_code == 307

    def forbidden_exchange(*args: object) -> None:
        pytest.fail("Invalid OAuth state reached the provider")

    monkeypatch.setattr(main, "exchange_google_code", forbidden_exchange)
    response = anonymous_client.get(
        "/auth/google/callback?state=forged&code=test-code",
        follow_redirects=False,
    )
    assert response.headers["location"].endswith("google_failed")


def test_google_network_failure_is_a_controlled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request

    original_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"Location": "file:///"})
    )
    monkeypatch.setattr(
        main.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs)
    )
    request = Request(
        {"type": "http", "headers": [], "scheme": "http", "server": ("testserver", 80)}
    )
    monkeypatch.setattr(settings, "google_redirect_uri", "http://testserver/auth/google/callback")
    with pytest.raises(HTTPException, match="Google"):
        main.exchange_google_code(request, "test-code")


@pytest.mark.parametrize("second_start", ["10:00:00", "10:15:00"])
def test_only_one_booking_can_win_a_race(raw_client: TestClient, second_start: str) -> None:
    raw_client.get("/booking")
    headers = csrf_headers(raw_client)

    def submit(start: str) -> int:
        return raw_client.post(
            "/api/appointments", json=booking(preferred_time=start), headers=headers,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ["10:00:00", second_start]))
    assert sorted(results) == [201, 409]


def test_cancel_releases_slot_and_reactivation_cannot_double_book(client: TestClient) -> None:
    first = client.post("/api/appointments", json=booking())
    assert first.status_code == 201
    appointment_id = first.json()["id"]
    date = booking()["preferred_date"]
    slots = client.get("/api/availability", params={"preferred_date": date}).json()
    assert "10:00:00" not in [slot["start_time"] for slot in slots]
    assert (
        client.post(
            f"/admin/appointments/{appointment_id}/status",
            data={"status": "canceled"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert client.post("/api/appointments", json=booking(preferred_time="10:15:00")).status_code == 201
    assert (
        client.post(
            f"/admin/appointments/{appointment_id}/status",
            data={"status": "confirmed"},
        ).status_code
        == 409
    )


def test_service_duration_controls_booking_and_preserves_existing_reservations(
    client: TestClient,
) -> None:
    short = "Перевірка зору"
    long = "Підбір окулярів"
    day = booking()["preferred_date"]

    def options(service: str) -> dict[str, str]:
        response = client.get("/api/availability", params={"preferred_date": day, "service": service})
        assert response.status_code == 200
        return {slot["start_time"]: slot["end_time"] for slot in response.json()}

    assert options(short)["12:30:00"] == "13:00:00"
    assert "12:30:00" not in options(long)
    assert options(long)["12:15:00"] == "13:00:00"
    assert client.post("/api/appointments", json=booking(
        service=long, preferred_time="12:30:00",
    )).status_code == 400
    assert client.post("/api/appointments", json=booking(service=short)).status_code == 201
    assert "10:15:00" not in options(long)
    assert options(long)["10:30:00"] == "11:15:00"
    assert client.post("/api/appointments", json=booking(
        service=long, preferred_time="10:15:00",
    )).status_code == 409

    item = next(row for row in client.get("/api/booking-services").json() if row["name"] == short)
    assert client.post(
        f"/admin/services/{item['id']}", data={"duration_minutes": 60, "is_active": "on"},
        follow_redirects=False,
    ).status_code == 303
    assert options(long)["10:30:00"] == "11:15:00"
    assert client.post("/api/appointments", json=booking(
        service=short, preferred_time="11:00:00", expected_duration_minutes=30,
    )).status_code == 409
    assert client.post("/api/appointments", json=booking(
        service=long, preferred_time="10:30:00", expected_duration_minutes=45,
    )).status_code == 201
    assert client.post(
        f"/admin/services/{item['id']}", data={"duration_minutes": 33, "is_active": "on"},
        follow_redirects=False,
    ).status_code == 303
    assert client.post("/api/appointments", json=booking(
        service=short, preferred_time="14:00:00", expected_duration_minutes=33,
    )).status_code == 201
    assert options(long)["14:33:00"] == "15:18:00"


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "   "},
        {"phone": "call me +380980000001"},
        {"phone": "123"},
        {"email": "not-an-email"},
        {"preferred_date": None},
        {"preferred_time": None},
        {"preferred_time": "10:00:01"},
        {"preferred_time": "10:00:00+02:00"},
    ],
)
def test_invalid_booking_rejected(anonymous_client: TestClient, changes: dict[str, object]) -> None:
    assert anonymous_client.post("/api/appointments", json=booking(**changes)).status_code == 422


def test_invalid_admin_data_does_not_reach_database(client: TestClient) -> None:
    assert (
        client.post(
            "/admin/services",
            data={
                "name": "Bad service",
                "duration_minutes": "-5",
                "price_uah": "-1",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/admin/time-slots",
            data={
                "start_time": "18:00",
                "end_time": "09:00",
            },
        ).status_code
        == 422
    )


def test_stored_html_is_escaped_in_admin(client: TestClient) -> None:
    attack = '<script>alert("xss")</script>'
    assert client.post("/api/appointments", json=booking(name=attack)).status_code == 201
    page = client.get("/admin/appointments")
    assert attack not in page.text
    assert "&lt;script&gt;" in page.text


def test_booking_spam_is_limited_and_ips_are_not_stored(
    anonymous_client: TestClient,
    app_db: Engine,
) -> None:
    for offset in range(10):
        response = anonymous_client.post(
            "/api/appointments",
            json=booking(
                preferred_date=(main.current_date() + timedelta(days=offset + 1)).isoformat(),
            ),
        )
        assert response.status_code == 201
    assert anonymous_client.post("/api/appointments", json=booking()).status_code == 429
    with Session(app_db) as session:
        buckets = session.exec(select(SecurityRateLimit)).all()
        assert buckets and all(len(bucket.key) == 64 for bucket in buckets)


def test_appointments_are_paginated(client: TestClient, app_db: Engine) -> None:
    with Session(app_db) as session:
        session.add_all(
            Appointment(
                name=f"Client {index}",
                phone="+380980000001",
                service="Consultation",
            )
            for index in range(55)
        )
        session.commit()
    first = client.get("/admin/appointments")
    second = client.get("/admin/appointments?page=2")
    assert "55 записів" in first.text
    assert 'href="/admin/appointments?page=2"' in first.text
    assert len(re.findall(r'action="/admin/appointments/[0-9]+/status"', first.text)) == 50
    assert len(re.findall(r'action="/admin/appointments/[0-9]+/status"', second.text)) == 5
    filtered = client.get("/admin/appointments", params={"q": "Client", "status": "new"})
    assert 'href="/admin/appointments?page=2&amp;q=Client&amp;status=new"' in filtered.text
    filtered_page = client.get(
        "/admin/appointments", params={"page": 2, "q": "Client", "status": "new"}
    )
    assert 'value="/admin/appointments?q=Client&amp;status=new&amp;page=2"' in filtered_page.text
    assert "Записів не знайдено" in client.get(
        "/admin/appointments", params={"q": "Client", "status": "canceled"}
    ).text
    assert "Записів не знайдено" in client.get(
        "/admin/appointments", params={"q": "%"}
    ).text
