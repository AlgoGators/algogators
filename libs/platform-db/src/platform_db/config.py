"""Canonical database configuration for AlgoGators services.

Adapted from algosystem's persistence config
(``libs/algosystem/src/algosystem/backtesting/infrastructure/persistence/config.py``).
algosystem keeps its own copy because it publishes to PyPI and must stay
installable outside the workspace; every workspace service depends on this one
instead of hand-rolling its own DB_* environment reading.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote_plus


class ConfigurationError(ValueError):
    """Raised when required database configuration is missing or invalid.

    Subclasses :class:`ValueError` deliberately: the hand-rolled readers this
    library replaces raised bare ``ValueError`` on missing DB_* variables, so
    callers that catch ``ValueError`` keep working unchanged.
    """


#: (environment variable, dataclass field) in the order ``from_env`` reads them.
_ENV_FIELDS: tuple[tuple[str, str], ...] = (
    ("DB_HOST", "host"),
    ("DB_PORT", "port"),
    ("DB_NAME", "database"),
    ("DB_USER", "user"),
    ("DB_PASSWORD", "password"),
)


@dataclass(frozen=True, repr=False)
class DatabaseConfig:
    """Validated database settings supplied explicitly by callers."""

    host: str
    port: int
    database: str
    user: str
    password: str

    def __post_init__(self) -> None:
        host = self._require_text("host", self.host)
        database = self._require_text("database", self.database)
        user = self._require_text("user", self.user)
        password = self._require_text("password", self.password)

        port = self._require_int("port", self.port)
        if port <= 0 or port > 65535:
            raise ConfigurationError("port must be between 1 and 65535")

        object.__setattr__(self, "host", host)
        object.__setattr__(self, "database", database)
        object.__setattr__(self, "user", user)
        object.__setattr__(self, "password", password)
        object.__setattr__(self, "port", port)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DatabaseConfig:
        """Build configuration from the required DB_* environment variables.

        Reads ``DB_HOST``, ``DB_PORT``, ``DB_NAME``, ``DB_USER`` and
        ``DB_PASSWORD`` from ``env`` (default: ``os.environ``). Every missing
        or blank variable is reported in a single error rather than only the
        first one found.
        """
        source: Mapping[str, str] = os.environ if env is None else env
        values: dict[str, str] = {}
        missing: list[str] = []
        for env_name, field_name in _ENV_FIELDS:
            raw = source.get(env_name)
            if raw is None or str(raw).strip() == "":
                missing.append(env_name)
            else:
                values[field_name] = str(raw)
        if missing:
            raise ConfigurationError(
                "Missing required database environment variables: " + ", ".join(missing)
            )
        return cls(
            host=values["host"],
            port=cls._require_int("port", values["port"]),
            database=values["database"],
            user=values["user"],
            password=values["password"],
        )

    def url(self) -> str:
        """Return a SQLAlchemy PostgreSQL connection URL.

        Credentials, host and database name are ``quote_plus``-escaped so a
        password containing ``@``, ``/`` or ``:`` cannot break the DSN.
        """
        return (
            "postgresql://"
            f"{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{quote_plus(self.host)}:{self.port}/{quote_plus(self.database)}"
        )

    def connect_kwargs(self) -> dict[str, str | int]:
        """Return the keyword arguments ``psycopg2.connect`` expects."""
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
        }

    def __repr__(self) -> str:
        return (
            "DatabaseConfig("
            f"host={self.host!r}, "
            f"port={self.port!r}, "
            f"database={self.database!r}, "
            f"user={self.user!r}, "
            "password='<redacted>')"
        )

    @staticmethod
    def _require_text(field_name: str, value: object) -> str:
        if value is None or str(value).strip() == "":
            raise ConfigurationError(f"missing database config field: {field_name}")
        return str(value)

    @staticmethod
    def _require_int(field_name: str, value: object) -> int:
        if value is None or str(value).strip() == "":
            raise ConfigurationError(f"missing database config field: {field_name}")
        try:
            return int(value)  # type: ignore[call-overload]
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"{field_name} must be an integer") from exc
