"""
YAML configuration loader and helpers.

Supports:
- Merging default/local/override configs
- Environment variable expansion (${VAR} or ${VAR:default})
- Mapping config sections to core dataclasses
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - handled at runtime
    yaml = None

from .costs import CostModel, DEFAULT_COST_MODEL
from .risk import RiskLimits


DEFAULT_CONFIG_PATH = Path("config/default.yaml")
LOCAL_CONFIG_PATH = Path("config/local.yaml")

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML is required to load config files. Install with `pip install PyYAML`.")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {path}")
    return data


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _expand_env_string(value: str) -> str:
    def _replace(match: re.Match) -> str:
        token = match.group(1)
        if ":" in token:
            var, default = token.split(":", 1)
        else:
            var, default = token, ""
        return os.getenv(var, default)

    return _ENV_PATTERN.sub(_replace, value)


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _expand_env_string(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(
    config_path: Optional[str] = None,
    base_path: Optional[Path] = DEFAULT_CONFIG_PATH,
    local_path: Optional[Path] = LOCAL_CONFIG_PATH,
) -> Dict[str, Any]:
    """
    Load config with overrides.

    Order:
      1) base_path (default config)
      2) local_path (developer overrides)
      3) config_path (explicit override)
    """
    config: Dict[str, Any] = {}
    if base_path is not None:
        config = _merge_dicts(config, _read_yaml(Path(base_path)))
    if local_path is not None:
        config = _merge_dicts(config, _read_yaml(Path(local_path)))
    if config_path:
        config = _merge_dicts(config, _read_yaml(Path(config_path)))
    return _expand_env(config)


def build_cost_model(config: Dict[str, Any]) -> CostModel:
    costs = config.get("costs", {}) or {}
    category = costs.get("category")
    if category:
        return CostModel.from_assumptions(category)

    base_spread_bps = costs.get("base_spread_bps")
    base_slippage_bps = costs.get("base_slippage_bps")
    impact = costs.get("impact_coefficient")
    max_impact = costs.get("max_impact")

    if base_spread_bps is None and base_slippage_bps is None and impact is None:
        return DEFAULT_COST_MODEL

    def _bps_to_decimal(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return float(value) / 10000.0

    return CostModel(
        base_spread=_bps_to_decimal(base_spread_bps) or DEFAULT_COST_MODEL.base_spread,
        base_slippage=_bps_to_decimal(base_slippage_bps) or DEFAULT_COST_MODEL.base_slippage,
        impact_coefficient=float(impact) if impact is not None else DEFAULT_COST_MODEL.impact_coefficient,
        max_impact=float(max_impact) if max_impact is not None else DEFAULT_COST_MODEL.max_impact,
    )


def build_risk_limits(config: Dict[str, Any]) -> RiskLimits:
    risk = config.get("risk", {}) or {}

    max_positions = risk.get("max_positions", None)
    max_positions_per_market = risk.get(
        "max_positions_per_market",
        risk.get("max_position_per_market", None),
    )

    max_holding_hours = risk.get("max_holding_hours", None)
    max_holding_days = risk.get("max_holding_days", None)
    if max_holding_days is None and max_holding_hours is not None:
        max_holding_days = max(1, int(float(max_holding_hours) / 24))

    limits = RiskLimits()
    for key, value in risk.items():
        if hasattr(limits, key):
            setattr(limits, key, value)

    if max_positions is not None:
        limits.max_total_positions = int(max_positions)
    if max_positions_per_market is not None:
        limits.max_positions_per_market = int(max_positions_per_market)
    if max_holding_days is not None:
        limits.max_holding_days = int(max_holding_days)

    if "max_daily_drawdown" in risk:
        limits.max_daily_drawdown_pct = float(risk["max_daily_drawdown"])
    if "max_weekly_drawdown" in risk:
        limits.max_weekly_drawdown_pct = float(risk["max_weekly_drawdown"])
    if "max_total_drawdown" in risk:
        limits.max_total_drawdown_pct = float(risk["max_total_drawdown"])
    if "max_spread" in risk:
        limits.max_spread_pct = float(risk["max_spread"])

    return limits


def apply_strategy_overrides(strategy: Any, overrides: Optional[Dict[str, Any]]) -> None:
    """
    Apply YAML overrides to a Strategy that owns a StrategyConfig.
    """
    if not overrides:
        return
    config = getattr(strategy, "config", None)
    if config is None:
        return

    if "enabled" in overrides:
        config.enabled = bool(overrides["enabled"])
    if "position_size" in overrides:
        config.position_size = float(overrides["position_size"])
    if "max_positions" in overrides:
        config.max_positions = int(overrides["max_positions"])
    if "confidence_threshold" in overrides:
        config.confidence_threshold = float(overrides["confidence_threshold"])

    reserved = {"enabled", "position_size", "max_positions", "confidence_threshold"}
    param_overrides = {k: v for k, v in overrides.items() if k not in reserved}
    if param_overrides:
        config.parameters.update(param_overrides)


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "LOCAL_CONFIG_PATH",
    "load_config",
    "build_cost_model",
    "build_risk_limits",
    "apply_strategy_overrides",
]
