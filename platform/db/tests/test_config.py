"""Tests for platform_db.DatabaseConfig and ConfigurationError."""

import dataclasses

import pytest
from platform_db import ConfigurationError, DatabaseConfig

FULL_ENV = {
    "DB_HOST": "db.internal.example.com",
    "DB_PORT": "6543",
    "DB_NAME": "algogators",
    "DB_USER": "algouser",
    "DB_PASSWORD": "s3cret",
}


@pytest.fixture
def db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete DB_* environment, isolated from the host machine's."""
    for key, value in FULL_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def no_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DB_* variables at all, regardless of the host machine's env."""
    for key in FULL_ENV:
        monkeypatch.delenv(key, raising=False)


# --- from_env -----------------------------------------------------------------


def test_from_env_reads_os_environ(db_env: None) -> None:
    config = DatabaseConfig.from_env()

    assert config.host == "db.internal.example.com"
    assert config.port == 6543
    assert config.database == "algogators"
    assert config.user == "algouser"
    assert config.password == "s3cret"


def test_from_env_accepts_explicit_mapping(no_db_env: None) -> None:
    config = DatabaseConfig.from_env(FULL_ENV)

    assert config.host == "db.internal.example.com"
    assert config.port == 6543


def test_from_env_port_is_int_checked(no_db_env: None) -> None:
    env = dict(FULL_ENV, DB_PORT="not-a-number")

    with pytest.raises(ConfigurationError, match="port must be an integer"):
        DatabaseConfig.from_env(env)


@pytest.mark.parametrize("missing_var", sorted(FULL_ENV))
def test_from_env_rejects_single_missing_var(
    monkeypatch: pytest.MonkeyPatch, db_env: None, missing_var: str
) -> None:
    monkeypatch.delenv(missing_var)

    with pytest.raises(
        ConfigurationError, match="Missing required database environment"
    ) as excinfo:
        DatabaseConfig.from_env()

    message = str(excinfo.value)
    assert missing_var in message
    for other in FULL_ENV:
        if other != missing_var:
            assert other not in message


def test_from_env_lists_every_missing_var_in_read_order(no_db_env: None) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        DatabaseConfig.from_env()

    assert "DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD" in str(excinfo.value)


def test_from_env_treats_blank_values_as_missing(no_db_env: None) -> None:
    env = dict(FULL_ENV, DB_PASSWORD="   ")

    with pytest.raises(ConfigurationError) as excinfo:
        DatabaseConfig.from_env(env)

    assert "DB_PASSWORD" in str(excinfo.value)


def test_configuration_error_is_a_value_error(no_db_env: None) -> None:
    # Callers that predate platform-db catch ValueError; the subclassing is
    # part of the public contract, not an implementation detail.
    with pytest.raises(ValueError):
        DatabaseConfig.from_env({})


# --- direct construction validation -------------------------------------------


def _make(**overrides: object) -> DatabaseConfig:
    kwargs: dict[str, object] = {
        "host": "localhost",
        "port": 5432,
        "database": "db",
        "user": "u",
        "password": "p",
    }
    kwargs.update(overrides)
    return DatabaseConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["host", "database", "user", "password"])
def test_blank_text_field_rejected(field: str) -> None:
    with pytest.raises(ConfigurationError, match=f"missing database config field: {field}"):
        _make(**{field: "  "})


@pytest.mark.parametrize("bad_port", [0, -1, 65536])
def test_port_out_of_range_rejected(bad_port: int) -> None:
    with pytest.raises(ConfigurationError, match="port must be between 1 and 65535"):
        _make(port=bad_port)


def test_port_none_rejected_as_missing() -> None:
    with pytest.raises(ConfigurationError, match="missing database config field: port"):
        _make(port=None)


def test_port_string_is_coerced_to_int() -> None:
    assert _make(port="5433").port == 5433


def test_config_is_frozen() -> None:
    config = _make()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.host = "elsewhere"  # type: ignore[misc]


# --- url ----------------------------------------------------------------------


def test_url_builds_sqlalchemy_dsn() -> None:
    config = _make()
    assert config.url() == "postgresql://u:p@localhost:5432/db"


def test_url_escapes_reserved_characters() -> None:
    config = _make(user="user@corp", password="p@ss/w:rd+")

    url = config.url()

    assert url == "postgresql://user%40corp:p%40ss%2Fw%3Ard%2B@localhost:5432/db"
    # The unescaped password must not appear anywhere in the DSN.
    assert "p@ss/w:rd+" not in url


# --- connect_kwargs -----------------------------------------------------------


def test_connect_kwargs_matches_psycopg2_signature() -> None:
    config = _make(port="6543")

    assert config.connect_kwargs() == {
        "host": "localhost",
        "port": 6543,
        "dbname": "db",
        "user": "u",
        "password": "p",
    }


# --- repr ---------------------------------------------------------------------


def test_repr_redacts_password() -> None:
    config = _make(password="super-secret")

    text = repr(config)

    assert "super-secret" not in text
    assert "password='<redacted>'" in text
    assert "host='localhost'" in text
    assert "port=5432" in text
