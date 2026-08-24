"""Contract validation for performance metrics JSON."""

from typing import Any


def validate_contract(obj: dict[str, Any]) -> list[str]:
    """
    Validate a metrics contract JSON object.

    The contract must have:
    - Required keys: probe_name, repo, probe_type, timestamp, status, metrics
    - status values restricted to: OK, WARN, FAIL, NO_DATA (when present)
    - metrics is a list of dicts with keys: id, value, unit

    Args:
        obj: The contract object to validate.

    Returns:
        List of validation error strings. Empty list means the object is valid.
    """
    errors: list[str] = []

    # Check required keys
    required_keys = {"probe_name", "repo", "probe_type", "timestamp", "status", "metrics"}
    missing_keys = required_keys - set(obj.keys())
    if missing_keys:
        for key in sorted(missing_keys):
            errors.append(f"Missing required key: {key}")

    # Check status value if present
    if "status" in obj:
        valid_statuses = {"OK", "WARN", "FAIL", "NO_DATA"}
        if obj["status"] not in valid_statuses:
            errors.append(
                f"Invalid status value: {obj['status']!r}. "
                f"Must be one of: {', '.join(sorted(valid_statuses))}"
            )

    # Check metrics structure
    if "metrics" in obj:
        if not isinstance(obj["metrics"], list):
            errors.append(f"metrics must be a list, got {type(obj['metrics']).__name__}")
        else:
            for i, metric in enumerate(obj["metrics"]):
                if not isinstance(metric, dict):
                    errors.append(f"metrics[{i}] must be a dict, got {type(metric).__name__}")
                else:
                    metric_required_keys = {"id", "value", "unit"}
                    metric_missing_keys = metric_required_keys - set(metric.keys())
                    if metric_missing_keys:
                        for key in sorted(metric_missing_keys):
                            errors.append(f"metrics[{i}] missing required key: {key}")

    return errors
