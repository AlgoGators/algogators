"""The access-gate seam between services and database credentials.

Services do not build a :class:`~platform_db.config.DatabaseConfig` directly;
they describe *who* wants *what* as an :class:`AccessRequest` and ask an
:class:`AccessGate` to authorize it. Today the only gate is
:class:`EnvAccessGate`, which grants every request from the process
environment — exactly the behaviour services had before the gate existed.

The point of the indirection is the IAM integration this package is moving
toward: an IAM-backed gate implements the same one-method protocol, checks the
request against centrally managed grants, and can return short-lived
per-principal credentials instead of the shared ``DB_*`` ones. Swapping the
gate changes no call sites.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from platform_db.config import ConfigurationError, DatabaseConfig

#: Actions a request may ask for. Deliberately coarse: fine-grained rules
#: (tables, schemas, row filters) belong to the IAM gate's policy store, not to
#: this transport type.
ACTIONS: tuple[str, ...] = ("read", "write", "admin")


class AccessDeniedError(PermissionError):
    """Raised by a gate that refuses a request.

    Subclasses :class:`PermissionError` so callers can catch the standard
    exception without importing this package.
    """

    def __init__(self, request: AccessRequest, reason: str) -> None:
        super().__init__(
            f"{request.principal} denied {request.action} on {request.database}: {reason}"
        )
        self.request = request
        self.reason = reason


@dataclass(frozen=True)
class AccessRequest:
    """Who wants which database, and for what.

    ``principal`` is the requesting identity — a service name today
    (``"data-ngin"``), an IAM principal identifier once the gate is backed by
    one. ``purpose`` is free text carried into audit logs, never into policy.
    """

    principal: str
    database: str
    action: str = "read"
    purpose: str = ""

    def __post_init__(self) -> None:
        for field_name in ("principal", "database"):
            value = getattr(self, field_name)
            if value is None or str(value).strip() == "":
                raise ConfigurationError(f"missing access request field: {field_name}")
            object.__setattr__(self, field_name, str(value).strip())
        if self.action not in ACTIONS:
            raise ConfigurationError(
                f"unknown action {self.action!r}; expected one of {', '.join(ACTIONS)}"
            )


@runtime_checkable
class AccessGate(Protocol):
    """Anything that can turn an :class:`AccessRequest` into credentials.

    Implementations either return a :class:`DatabaseConfig` for the requested
    database or raise :class:`AccessDeniedError`. Structural protocol: an IAM
    client needs no import from here beyond the two data types.
    """

    def authorize(self, request: AccessRequest) -> DatabaseConfig: ...


class EnvAccessGate:
    """Allow-all gate that grants every request from ``DB_*`` environment.

    The pre-gate behaviour behind the gate interface: no policy, one shared
    credential. Exists so services can migrate to the ``authorize`` call path
    now and swap in an IAM-backed gate later without touching call sites.
    """

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env

    def authorize(self, request: AccessRequest) -> DatabaseConfig:
        config = DatabaseConfig.from_env(self._env)
        if config.database != request.database:
            raise AccessDeniedError(
                request,
                f"environment is configured for database {config.database!r}",
            )
        return config
