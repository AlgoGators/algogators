"""Tests for contract validation."""

from infra.perf.contract import validate_contract


class TestContractValidation:
    """Test contract.validate_contract()."""

    def test_valid_contract(self) -> None:
        """A valid contract should pass validation."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123"},
            "metrics": [
                {"id": "metric1", "value": 100, "unit": "bytes"},
                {"id": "metric2", "value": 50.5, "unit": "percent"},
            ],
        }
        errors = validate_contract(contract)
        assert errors == []

    def test_valid_contract_empty_metrics(self) -> None:
        """Empty metrics list is valid (means ran but found nothing)."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123"},
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert errors == []

    def test_missing_suite(self) -> None:
        """Missing suite should fail."""
        contract = {
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123"},
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "Missing required key: suite" in errors

    def test_missing_multiple_keys(self) -> None:
        """Missing multiple keys should fail for each."""
        contract = {
            "suite": "perf",
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert len(errors) == 4
        assert "Missing required key: captured_at" in errors
        assert "Missing required key: environment" in errors
        assert "Missing required key: probe" in errors
        assert "Missing required key: repo" in errors

    def test_missing_environment_host(self) -> None:
        """Missing environment.host should fail."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"commit": "abc123"},
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "environment missing required key: host" in errors

    def test_missing_environment_commit(self) -> None:
        """Missing environment.commit should fail."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host"},
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "environment missing required key: commit" in errors

    def test_environment_not_dict(self) -> None:
        """Environment must be a dict."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": "not-a-dict",
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "environment must be a dict" in errors[0]

    def test_metrics_not_list(self) -> None:
        """Metrics must be a list."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123"},
            "metrics": {"id": "metric1"},
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "metrics must be a list" in errors[0]

    def test_metric_not_dict(self) -> None:
        """Each metric must be a dict."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123"},
            "metrics": ["not a dict"],
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "metrics[0] must be a dict" in errors[0]

    def test_metric_missing_id(self) -> None:
        """Metric missing id field should fail."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123"},
            "metrics": [
                {"value": 100, "unit": "bytes"},
            ],
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "metrics[0] missing required key: id" in errors[0]

    def test_metric_missing_multiple_fields(self) -> None:
        """Metric missing multiple fields should fail for each."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123"},
            "metrics": [
                {"id": "metric1"},
            ],
        }
        errors = validate_contract(contract)
        assert len(errors) == 2
        assert "metrics[0] missing required key: unit" in errors
        assert "metrics[0] missing required key: value" in errors

    def test_multiple_metrics_mixed_errors(self) -> None:
        """Multiple metrics with mixed errors."""
        contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "test_probe",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123"},
            "metrics": [
                {"id": "metric1", "value": 100, "unit": "bytes"},  # valid
                {"id": "metric2", "value": 50},  # missing unit
                {"value": 30, "unit": "percent"},  # missing id
            ],
        }
        errors = validate_contract(contract)
        assert len(errors) == 2
        assert any("metrics[1]" in e for e in errors)
        assert any("metrics[2]" in e for e in errors)
