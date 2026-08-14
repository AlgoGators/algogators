"""Tests for create_app() composition branches (infrastructure/config/app_factory.py).

The production fail-closed guards are exercised at import time by
test_security.py subprocesses; here we call create_app() directly in-process to
cover the dev-mode branches (ephemeral JWT secret, DEV_MODE warnings, log-dir
creation), the CORS wiring, the JWT error loaders, and the health endpoint.
"""

import contextlib
import logging
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from flask_jwt_extended import create_access_token

import research_api.app as app_module
import research_api.infrastructure.config.app_factory as app_factory

FACTORY_LOGGER = "research_api.infrastructure.config.app_factory"


@contextlib.contextmanager
def _fresh_app():
    """Create an app and detach any log handlers create_app added on exit.

    app.logger is shared by name across create_app() calls, so without cleanup
    every test would leak handlers (and hold log files open on Windows).
    """
    logger = logging.getLogger(FACTORY_LOGGER)
    before = list(logger.handlers)
    try:
        yield app_factory.create_app()
    finally:
        for handler in list(logger.handlers):
            if handler not in before:
                handler.close()
                logger.removeHandler(handler)


@pytest.fixture
def dev_env(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("FLASK_DEBUG", "true")  # skip the file handler branch
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-for-pytest")
    monkeypatch.delenv("DEV_MODE", raising=False)


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("FLASK_DEBUG", "true")  # skip the file handler branch
    monkeypatch.setenv("JWT_SECRET_KEY", "a-production-grade-secret")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    monkeypatch.delenv("DEV_MODE", raising=False)


# --- environment flags --------------------------------------------------------


def test_dev_app_records_environment_flags(dev_env):
    with _fresh_app() as app:
        assert app.config["ALGOLENS_ENV"] == "development"
        assert app.config["ALGOLENS_IS_PRODUCTION"] is False
        assert app.config["ALGOLENS_DEBUG"] is True
        assert app.config["JWT_COOKIE_SECURE"] is False


def test_dev_mode_active_warning_logged(dev_env, monkeypatch, caplog):
    monkeypatch.setenv("DEV_MODE", "1")
    with caplog.at_level(logging.WARNING, logger=FACTORY_LOGGER), _fresh_app():
        pass
    assert "DEV_MODE=1 active" in caplog.text


def test_dev_mode_flag_neutralized_in_production(prod_env, monkeypatch, caplog):
    monkeypatch.setenv("DEV_MODE", "1")
    with caplog.at_level(logging.WARNING, logger=FACTORY_LOGGER), _fresh_app() as app:
        assert app.config["ALGOLENS_IS_PRODUCTION"] is True
    assert "DISABLED for safety" in caplog.text


# --- JWT secret handling ------------------------------------------------------


def test_dev_app_generates_ephemeral_jwt_secret(dev_env, monkeypatch, caplog):
    monkeypatch.delenv("JWT_SECRET_KEY")
    with caplog.at_level(logging.WARNING, logger=FACTORY_LOGGER), _fresh_app() as app:
        secret = app.config["JWT_SECRET_KEY"]
    assert secret  # a usable key was generated
    assert len(secret) >= 32
    assert "ephemeral dev key" in caplog.text


def test_production_refuses_missing_jwt_secret_in_process(prod_env, monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY")
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY must be set"), _fresh_app():
        pass


# --- CORS ---------------------------------------------------------------------


@pytest.mark.parametrize("origins", ["", "   ", "*"])
def test_production_refuses_wildcard_or_empty_cors_in_process(prod_env, monkeypatch, origins):
    monkeypatch.setenv("CORS_ORIGINS", origins)
    with pytest.raises(RuntimeError, match="CORS_ORIGINS must be set"), _fresh_app():
        pass


def test_production_cors_allows_only_listed_origins(prod_env, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://one.example.com, https://two.example.com,")
    with _fresh_app() as app:
        app.config.update(TESTING=True)
        client = app.test_client()

        allowed = client.get("/nonexistent", headers={"Origin": "https://two.example.com"})
        assert allowed.headers.get("Access-Control-Allow-Origin") == "https://two.example.com"

        denied = client.get("/nonexistent", headers={"Origin": "https://evil.example.com"})
        assert denied.status_code == 404
        assert denied.headers.get("Access-Control-Allow-Origin") is None


# --- logging setup ------------------------------------------------------------


def test_non_debug_app_creates_logs_directory_and_file(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-for-pytest")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("DEV_MODE", raising=False)
    monkeypatch.chdir(tmp_path)

    with _fresh_app() as app:
        assert app.debug is False
        assert (tmp_path / "logs").is_dir()
        assert (tmp_path / "logs" / "research_api.log").exists()


# --- request/response hooks on the singleton app ------------------------------


def test_preflight_options_request_is_handled(client):
    resp = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"


def test_health_reports_ok_when_database_reachable(client, monkeypatch):
    conn = MagicMock()
    monkeypatch.setattr(app_factory, "get_db_connection", lambda: conn)

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    # development responses include diagnostics
    assert body["environment"] == "development"
    conn.cursor.return_value.__enter__.return_value.execute.assert_called_once_with("SELECT 1")
    conn.close.assert_called_once()


def test_health_degrades_with_error_detail_in_development(client, monkeypatch):
    def boom():
        raise RuntimeError("no database here")

    monkeypatch.setattr(app_factory, "get_db_connection", boom)

    resp = client.get("/health")

    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "error: no database here"


# --- JWT error loaders --------------------------------------------------------


def test_garbage_token_hits_invalid_token_loader(client):
    client.set_cookie("access_token_cookie", "definitely-not-a-jwt")

    resp = client.get("/auth/verify")

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Invalid or expired token. Please log in again."


def test_expired_token_hits_expired_token_loader(client):
    with app_module.app.app_context():
        token = create_access_token(identity="1", expires_delta=timedelta(seconds=-10))
    client.set_cookie("access_token_cookie", token)

    resp = client.get("/auth/verify")

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Token has expired. Please log in again."
