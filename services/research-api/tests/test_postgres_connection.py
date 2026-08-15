"""Unit tests for the Postgres connection helpers (infrastructure/db/postgres.py).

No live database: socket resolution and psycopg2.connect are patched, and the
missing-env-var fail-fast paths are driven with monkeypatch.delenv.
"""

import logging
import socket
from unittest.mock import MagicMock

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor
from research_api.infrastructure.db import postgres

LOGGER_NAME = "research_api.infrastructure.db.postgres"

FULL_ENV = {
    "DB_HOST": "db.internal.example.com",
    "DB_PORT": "6543",
    "DB_USER": "algouser",
    "DB_PASSWORD": "s3cret",
    "DB_NAME": "algolens",
}


@pytest.fixture
def db_env(monkeypatch):
    for key, value in FULL_ENV.items():
        monkeypatch.setenv(key, value)


class FakeConnect:
    def __init__(self, result=None, error=None):
        self.result = result if result is not None else MagicMock(name="connection")
        self.error = error
        self.kwargs: dict[str, object] | None = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.result


# --- missing environment variables -------------------------------------------


@pytest.mark.parametrize("missing_var", ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"])
def test_get_db_connection_rejects_single_missing_var(monkeypatch, db_env, missing_var):
    monkeypatch.delenv(missing_var)

    with pytest.raises(ValueError, match="Missing required database environment") as excinfo:
        postgres.get_db_connection()

    message = str(excinfo.value)
    assert missing_var in message
    for other in FULL_ENV:
        if other not in (missing_var, "DB_PORT"):
            assert other not in message


def test_get_db_connection_lists_every_missing_var(monkeypatch):
    for key in FULL_ENV:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValueError) as excinfo:
        postgres.get_db_connection()

    # DatabaseConfig.from_env reads (and therefore reports) the variables in
    # DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD order; DB_PORT is absent
    # because get_db_connection defaults it to 5432 before validation.
    message = str(excinfo.value)
    assert "DB_HOST, DB_NAME, DB_USER, DB_PASSWORD" in message


# --- successful connections ---------------------------------------------------


def test_get_db_connection_passes_env_config_to_psycopg2(monkeypatch, db_env):
    fake = FakeConnect()
    monkeypatch.setattr(postgres.socket, "gethostbyname", lambda host: "10.0.0.5")
    monkeypatch.setattr(postgres.psycopg2, "connect", fake)

    conn = postgres.get_db_connection()

    assert conn is fake.result
    assert fake.kwargs == {
        "host": "db.internal.example.com",
        "port": 6543,
        "user": "algouser",
        "password": "s3cret",
        "dbname": "algolens",
        "cursor_factory": RealDictCursor,
        "connect_timeout": 10,
    }


def test_get_db_connection_defaults_port_to_5432(monkeypatch, db_env):
    monkeypatch.delenv("DB_PORT")
    fake = FakeConnect()
    monkeypatch.setattr(postgres.socket, "gethostbyname", lambda host: "10.0.0.5")
    monkeypatch.setattr(postgres.psycopg2, "connect", fake)

    postgres.get_db_connection()

    assert fake.kwargs is not None
    assert fake.kwargs["port"] == 5432


def test_dns_failure_is_logged_but_connection_still_attempted(monkeypatch, db_env, caplog):
    def raise_gaierror(host):
        raise socket.gaierror("Name or service not known")

    fake = FakeConnect()
    monkeypatch.setattr(postgres.socket, "gethostbyname", raise_gaierror)
    monkeypatch.setattr(postgres.psycopg2, "connect", fake)

    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        conn = postgres.get_db_connection()

    assert conn is fake.result
    assert "DNS resolution failed" in caplog.text


# --- OperationalError diagnosis ----------------------------------------------


@pytest.mark.parametrize(
    ("error_message", "diagnosis"),
    [
        ("could not connect to server: Connection refused", "Database server unreachable"),
        ('FATAL: password authentication failed for user "algouser"', "Invalid credentials"),
        ('FATAL: database "algolens" does not exist', "Database does not exist"),
        ("connection timeout expired", "Connection timeout"),
    ],
)
def test_operational_error_diagnosis_logged_and_reraised(
    monkeypatch, db_env, caplog, error_message, diagnosis
):
    fake = FakeConnect(error=psycopg2.OperationalError(error_message))
    monkeypatch.setattr(postgres.socket, "gethostbyname", lambda host: "10.0.0.5")
    monkeypatch.setattr(postgres.psycopg2, "connect", fake)

    with (
        caplog.at_level(logging.ERROR, logger=LOGGER_NAME),
        pytest.raises(psycopg2.OperationalError),
    ):
        postgres.get_db_connection()

    assert f">>> DIAGNOSIS: {diagnosis}" in caplog.text


def test_operational_error_without_known_pattern_has_no_diagnosis(monkeypatch, db_env, caplog):
    fake = FakeConnect(error=psycopg2.OperationalError("some entirely novel failure"))
    monkeypatch.setattr(postgres.socket, "gethostbyname", lambda host: "10.0.0.5")
    monkeypatch.setattr(postgres.psycopg2, "connect", fake)

    with (
        caplog.at_level(logging.ERROR, logger=LOGGER_NAME),
        pytest.raises(psycopg2.OperationalError),
    ):
        postgres.get_db_connection()

    assert ">>> DIAGNOSIS" not in caplog.text
    assert "some entirely novel failure" in caplog.text


# --- execute_query ------------------------------------------------------------


@pytest.fixture
def fake_conn(monkeypatch):
    conn = MagicMock(name="connection")
    monkeypatch.setattr(postgres, "get_db_connection", lambda: conn)
    return conn


def _cursor(conn):
    return conn.cursor.return_value.__enter__.return_value


def test_execute_query_select_fetches_all_rows(fake_conn):
    _cursor(fake_conn).fetchall.return_value = [{"id": 1}, {"id": 2}]

    result = postgres.execute_query("SELECT * FROM auth.users")

    assert result == [{"id": 1}, {"id": 2}]
    _cursor(fake_conn).execute.assert_called_once_with("SELECT * FROM auth.users", None)
    fake_conn.commit.assert_not_called()
    fake_conn.close.assert_called_once()


def test_execute_query_select_fetch_one(fake_conn):
    _cursor(fake_conn).fetchone.return_value = {"id": 1}

    result = postgres.execute_query("SELECT * FROM auth.users WHERE id = %s", (1,), fetch_one=True)

    assert result == {"id": 1}
    _cursor(fake_conn).execute.assert_called_once_with(
        "SELECT * FROM auth.users WHERE id = %s", (1,)
    )
    fake_conn.close.assert_called_once()


def test_execute_query_detects_select_despite_whitespace_and_case(fake_conn):
    _cursor(fake_conn).fetchall.return_value = []

    result = postgres.execute_query("   select 1")

    assert result == []
    fake_conn.commit.assert_not_called()


def test_execute_query_non_select_commits_and_returns_rowcount(fake_conn):
    _cursor(fake_conn).rowcount = 3

    result = postgres.execute_query("UPDATE auth.users SET role = %s", ("admin",))

    assert result == 3
    fake_conn.commit.assert_called_once()
    fake_conn.close.assert_called_once()


def test_execute_query_closes_connection_when_execute_raises(fake_conn):
    _cursor(fake_conn).execute.side_effect = RuntimeError("bad query")

    with pytest.raises(RuntimeError, match="bad query"):
        postgres.execute_query("SELECT 1")

    fake_conn.close.assert_called_once()
