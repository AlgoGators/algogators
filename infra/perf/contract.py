"""Contract validation for performance metrics JSON."""

from typing import Any


def validate_contract(obj: dict[str, Any]) -> list[str]:
    """
    Validate a metrics contract JSON object per spec §3.

    The contract must have:
    - Required keys: suite, repo, probe, captured_at, environment, metrics
    - environment is a dict with: host, commit
    - metrics is a list of dicts with keys: id, value, unit
    - Status should NOT be present at this layer (status is report.json-only)

    Args:
        obj: The contract object to validate.

    Returns:
        List of validation error strings. Empty list means the object is valid.
    """
    errors: list[str] = []

    # Check required keys
    required_keys = {"suite", "repo", "probe", "captured_at", "environment", "metrics"}
    missing_keys = required_keys - set(obj.keys())
    if missing_keys:
        for key in sorted(missing_keys):
            errors.append(f"Missing required key: {key}")

    # Check environment structure
    if "environment" in obj:
        if not isinstance(obj["environment"], dict):
            errors.append(f"environment must be a dict, got {type(obj['environment']).__name__}")
        else:
            env_required = {"host", "commit"}
            env_missing = env_required - set(obj["environment"].keys())
            if env_missing:
                for key in sorted(env_missing):
                    errors.append(f"environment missing required key: {key}")

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
