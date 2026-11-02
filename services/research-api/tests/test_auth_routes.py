"""Route-level tests for the auth blueprint (adapters/http/auth.py).

All DB access is stubbed by monkeypatching the module-level dependency seams
(`execute_query`, `get_db_connection`, hash functions) that the routes wire
into their use cases per-request.
"""

from unittest.mock import MagicMock

import pytest
import research_api.adapters.http.auth as auth_mod
import research_api.app as app_module
from flask_jwt_extended import create_access_token


def _user_row(**overrides):
    row = {
        "id": 7,
        "email": "member@example.com",
        "first_name": "Mem",
        "last_name": "Ber",
        "role": "general_member",
        "password_hash": "pbkdf2:something",
    }
    row.update(overrides)
    return row


def _set_session_cookie(client, identity):
    with app_module.app.app_context():
        token = create_access_token(identity=str(identity))
    client.set_cookie("access_token_cookie", token)


@pytest.fixture
def dev_mode_off(monkeypatch):
    monkeypatch.delenv("DEV_MODE", raising=False)
    monkeypatch.setenv("FLASK_ENV", "development")


@pytest.fixture
def dev_mode_on(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "1")
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("DEV_USER_ID", "1")
    monkeypatch.delenv("DEV_USER_EMAIL", raising=False)
    monkeypatch.delenv("DEV_USER_ROLE", raising=False)


# --- helpers ------------------------------------------------------------------


def test_dev_mode_enabled_helper_tracks_environment(dev_mode_on, monkeypatch):
    assert auth_mod._dev_mode_enabled() is True
    monkeypatch.setenv("FLASK_ENV", "production")
    assert auth_mod._dev_mode_enabled() is False


# --- /auth/login --------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": "member@example.com"},
        {"password": "a" * 12},
        {"email": "", "password": "a" * 12},
    ],
)
def test_login_requires_email_and_password(client, payload):
    resp = client.post("/auth/login", json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Email and password are required"


def test_login_unexpected_error_returns_masked_500(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("connection pool exhausted")

    monkeypatch.setattr(auth_mod, "execute_query", boom)

    resp = client.post("/auth/login", json={"email": "member@example.com", "password": "a" * 12})

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["error"] == "An internal error occurred during login"
    assert "connection pool" not in str(body)  # internals must not leak


# --- /auth/verify -------------------------------------------------------------


def test_verify_returns_db_user_when_dev_mode_off(client, monkeypatch, dev_mode_off):
    monkeypatch.setattr(auth_mod, "execute_query", lambda *a, **k: _user_row())
    _set_session_cookie(client, 7)

    resp = client.get("/auth/verify")

    assert resp.status_code == 200
    user = resp.get_json()["user"]
    assert user["id"] == 7
    assert user["email"] == "member@example.com"
    assert "password_hash" not in user


def test_verify_unknown_user_returns_404(client, monkeypatch, dev_mode_off):
    monkeypatch.setattr(auth_mod, "execute_query", lambda *a, **k: None)
    _set_session_cookie(client, 12345)

    resp = client.get("/auth/verify")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "User not found"


def test_verify_short_circuits_to_dev_user_when_identity_matches(client, monkeypatch, dev_mode_on):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("dev-mode verify must not touch the database")

    monkeypatch.setattr(auth_mod, "execute_query", fail_if_called)
    _set_session_cookie(client, 1)  # DEV_USER_ID defaults to 1

    resp = client.get("/auth/verify")

    assert resp.status_code == 200
    user = resp.get_json()["user"]
    assert user["id"] == 1
    assert user["email"] == "dev@research_api.local"
    assert user["role"] == "admin"


def test_verify_falls_through_to_db_when_dev_identity_mismatch(client, monkeypatch, dev_mode_on):
    monkeypatch.setattr(auth_mod, "execute_query", lambda *a, **k: _user_row(id=31))
    _set_session_cookie(client, 31)

    resp = client.get("/auth/verify")

    assert resp.status_code == 200
    assert resp.get_json()["user"]["id"] == 31


# --- /auth/dev-login ----------------------------------------------------------


def test_dev_login_falls_back_to_id_1_when_dev_user_id_not_integer(
    client, monkeypatch, dev_mode_on
):
    monkeypatch.setenv("DEV_USER_ID", "not-an-integer")

    resp = client.post("/auth/dev-login")

    assert resp.status_code == 200
    user = resp.get_json()["user"]
    assert user["id"] == 1
    assert user["email"] == "dev@research_api.local"
    assert user["role"] == "admin"


# --- /auth/check-email --------------------------------------------------------


def test_check_email_requires_email(client):
    resp = client.post("/auth/check-email", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Email is required"


def test_check_email_unknown_email_returns_404(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "execute_query", lambda *a, **k: None)

    resp = client.post("/auth/check-email", json={"email": "nobody@example.com"})

    assert resp.status_code == 404
    body = resp.get_json()
    assert body["exists"] is False
    assert "registered" not in body
    assert "Contact an administrator" in body["message"]


def test_check_email_registered_account(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "execute_query", lambda *a, **k: _user_row())

    resp = client.post("/auth/check-email", json={"email": "member@example.com"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["exists"] is True
    assert body["registered"] is True
    assert "Please login" in body["message"]


def test_check_email_preauthorized_but_unregistered(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "execute_query", lambda *a, **k: _user_row(password_hash=None))

    resp = client.post("/auth/check-email", json={"email": "member@example.com"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["exists"] is True
    assert body["registered"] is False
    assert "complete registration" in body["message"]


# --- /auth/register -----------------------------------------------------------

VALID_REGISTRATION = {
    "email": "member@example.com",
    "password": "a-long-enough-password",
    "first_name": "Mem",
    "last_name": "Ber",
}


@pytest.mark.parametrize("dropped", ["email", "password", "first_name", "last_name"])
def test_register_requires_all_fields(client, dropped):
    payload = {k: v for k, v in VALID_REGISTRATION.items() if k != dropped}

    resp = client.post("/auth/register", json=payload)

    assert resp.status_code == 400
    assert "required" in resp.get_json()["error"]


def test_register_unauthorized_email_returns_403(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "execute_query", lambda *a, **k: None)

    resp = client.post("/auth/register", json=VALID_REGISTRATION)

    assert resp.status_code == 403
    assert "not authorized" in resp.get_json()["error"]


def test_register_already_registered_returns_400(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "execute_query", lambda *a, **k: _user_row())

    resp = client.post("/auth/register", json=VALID_REGISTRATION)

    assert resp.status_code == 400
    assert "already registered" in resp.get_json()["error"]


def test_register_success_completes_registration_and_sets_cookie(client, monkeypatch):
    # Pre-authorized row: exists but has no password yet.
    monkeypatch.setattr(auth_mod, "execute_query", lambda *a, **k: _user_row(password_hash=None))
    monkeypatch.setattr(auth_mod, "generate_password_hash", lambda pw: f"hashed:{pw}")

    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {
        "id": 7,
        "email": "member@example.com",
        "first_name": "Mem",
        "last_name": "Ber",
        "role": "general_member",
    }
    monkeypatch.setattr(auth_mod, "get_db_connection", lambda: conn)

    resp = client.post("/auth/register", json=VALID_REGISTRATION)

    assert resp.status_code == 201
    body = resp.get_json()
    assert "token" not in body  # session travels only in the cookie
    assert body["user"]["email"] == "member@example.com"
    assert body["user"]["first_name"] == "Mem"

    _query, params = cursor.execute.call_args.args
    assert params == ("hashed:a-long-enough-password", "Mem", "Ber", "member@example.com")
    conn.commit.assert_called_once()
    conn.close.assert_called_once()

    set_cookies = resp.headers.getlist("Set-Cookie")
    access = [c for c in set_cookies if c.startswith("access_token_cookie=")]
    assert access and "HttpOnly" in access[0]
