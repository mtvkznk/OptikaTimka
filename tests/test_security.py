import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

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
from app.security import SESSION_COOKIE_NAME, cookie_name


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
                token_hash=main.hash_session_token(token),
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
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "HttpOnly" in response.headers["set-cookie"]


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


@pytest.mark.parametrize(
    "changes",
    [
        {"auth_secret": "short"},
        {"admin_password": "short"},
        {"public_base_url": "http://optika.example"},
        {"public_base_url": None},
        {"phone_auth_debug": True},
        {"environment": "prodction"},
        {"public_base_url": "https://optika.example/path"},
    ],
)
def test_unsafe_production_configuration_rejected(changes: dict[str, object]) -> None:
    options = {
        "environment": "production",
        "auth_secret": "test-only-secret-" * 4,
        "admin_password": "test-only-admin-password-123",
        "public_base_url": "https://optika.example",
        "phone_auth_debug": False,
        **changes,
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **options)


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
    def forbidden_exchange(*args: object) -> None:
        pytest.fail("Invalid OAuth state reached the provider")

    monkeypatch.setattr(main, "exchange_google_code", forbidden_exchange)
    response = anonymous_client.get(
        "/auth/google/callback?state=forged&code=test-code",
        follow_redirects=False,
    )
    assert response.headers["location"].endswith("google_failed")


def test_google_network_failure_is_a_controlled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
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


def test_only_one_booking_can_win_a_race(raw_client: TestClient) -> None:
    raw_client.get("/")
    headers = csrf_headers(raw_client)

    def submit() -> int:
        return raw_client.post("/api/appointments", json=booking(), headers=headers).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit(), range(2)))
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
    assert client.post("/api/appointments", json=booking()).status_code == 201
    assert (
        client.post(
            f"/admin/appointments/{appointment_id}/status",
            data={"status": "confirmed"},
        ).status_code
        == 409
    )


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
