"""Tests for the access-gate seam."""

from __future__ import annotations

import pytest
from platform_db import (
    AccessDeniedError,
    AccessGate,
    AccessRequest,
    ConfigurationError,
    DatabaseConfig,
    EnvAccessGate,
)

ENV = {
    "DB_HOST": "db.internal",
    "DB_PORT": "5432",
    "DB_NAME": "markets",
    "DB_USER": "svc",
    "DB_PASSWORD": "s3cret",
}


class TestAccessRequest:
    def test_valid_request(self) -> None:
        req = AccessRequest(principal="data-ngin", database="markets", action="write")
        assert req.principal == "data-ngin"
        assert req.action == "write"

    def test_default_action_is_read(self) -> None:
        assert AccessRequest(principal="x", database="y").action == "read"

    def test_strips_whitespace(self) -> None:
        req = AccessRequest(principal="  data-ngin ", database=" markets ")
        assert req.principal == "data-ngin"
        assert req.database == "markets"

    @pytest.mark.parametrize("field", ["principal", "database"])
    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_missing_identity_fields_rejected(self, field: str, bad: object) -> None:
        kwargs = {"principal": "p", "database": "d", field: bad}
        with pytest.raises(ConfigurationError, match=field):
            AccessRequest(**kwargs)  # type: ignore[arg-type]

    def test_unknown_action_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="unknown action"):
            AccessRequest(principal="p", database="d", action="drop-tables")

    def test_configuration_error_is_value_error(self) -> None:
        with pytest.raises(ValueError):
            AccessRequest(principal="", database="d")


class TestEnvAccessGate:
    def test_grants_matching_database(self) -> None:
        gate = EnvAccessGate(ENV)
        config = gate.authorize(AccessRequest(principal="data-ngin", database="markets"))
        assert isinstance(config, DatabaseConfig)
        assert config.host == "db.internal"
        assert config.database == "markets"

    def test_denies_mismatched_database(self) -> None:
        gate = EnvAccessGate(ENV)
        request = AccessRequest(principal="data-ngin", database="people")
        with pytest.raises(AccessDeniedError) as exc_info:
            gate.authorize(request)
        assert exc_info.value.request is request
        assert "markets" in exc_info.value.reason

    def test_denial_is_a_permission_error(self) -> None:
        gate = EnvAccessGate(ENV)
        with pytest.raises(PermissionError):
            gate.authorize(AccessRequest(principal="p", database="other"))

    def test_missing_environment_raises_configuration_error(self) -> None:
        gate = EnvAccessGate({})
        with pytest.raises(ConfigurationError):
            gate.authorize(AccessRequest(principal="p", database="markets"))

    def test_satisfies_gate_protocol(self) -> None:
        assert isinstance(EnvAccessGate(ENV), AccessGate)


class TestCustomGate:
    def test_structural_protocol_accepts_foreign_implementation(self) -> None:
        """An IAM client class needs no inheritance from this package."""

        class DenyAllGate:
            def authorize(self, request: AccessRequest) -> DatabaseConfig:
                raise AccessDeniedError(request, "no grants configured")

        gate = DenyAllGate()
        assert isinstance(gate, AccessGate)
        with pytest.raises(AccessDeniedError, match="no grants configured"):
            gate.authorize(AccessRequest(principal="p", database="d"))
