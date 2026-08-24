"""Tests for contract validation."""


from infra.perf.contract import validate_contract


class TestContractValidation:
    """Test contract.validate_contract()."""

    def test_valid_contract(self) -> None:
        """A valid contract should pass validation."""
        contract = {
            "probe_name": "test_probe",
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "OK",
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
            "probe_name": "test_probe",
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "OK",
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert errors == []

    def test_valid_contract_no_data_status(self) -> None:
        """NO_DATA status is valid."""
        contract = {
            "probe_name": "test_probe",
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "NO_DATA",
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert errors == []

    def test_missing_probe_name(self) -> None:
        """Missing probe_name should fail."""
        contract = {
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "OK",
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "Missing required key: probe_name" in errors

    def test_missing_multiple_keys(self) -> None:
        """Missing multiple keys should fail for each."""
        contract = {
            "probe_name": "test_probe",
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert len(errors) == 4
        assert "Missing required key: probe_type" in errors
        assert "Missing required key: repo" in errors
        assert "Missing required key: status" in errors
        assert "Missing required key: timestamp" in errors

    def test_invalid_status(self) -> None:
        """Invalid status value should fail."""
        contract = {
            "probe_name": "test_probe",
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "INVALID",
            "metrics": [],
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "Invalid status value: 'INVALID'" in errors[0]
        assert "OK" in errors[0] and "WARN" in errors[0] and "FAIL" in errors[0]

    def test_valid_status_values(self) -> None:
        """All valid status values should pass."""
        for status in ["OK", "WARN", "FAIL", "NO_DATA"]:
            contract = {
                "probe_name": "test_probe",
                "repo": "test_repo",
                "probe_type": "test",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "status": status,
                "metrics": [],
            }
            errors = validate_contract(contract)
            assert errors == [], f"Status {status} should be valid"

    def test_metrics_not_list(self) -> None:
        """Metrics must be a list."""
        contract = {
            "probe_name": "test_probe",
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "OK",
            "metrics": {"id": "metric1"},
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "metrics must be a list" in errors[0]

    def test_metric_not_dict(self) -> None:
        """Each metric must be a dict."""
        contract = {
            "probe_name": "test_probe",
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "OK",
            "metrics": ["not a dict"],
        }
        errors = validate_contract(contract)
        assert len(errors) == 1
        assert "metrics[0] must be a dict" in errors[0]

    def test_metric_missing_id(self) -> None:
        """Metric missing id field should fail."""
        contract = {
            "probe_name": "test_probe",
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "OK",
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
            "probe_name": "test_probe",
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "OK",
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
            "probe_name": "test_probe",
            "repo": "test_repo",
            "probe_type": "test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "status": "OK",
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
