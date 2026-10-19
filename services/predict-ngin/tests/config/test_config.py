from pathlib import Path

import pytest

import importlib

from trading.config import build_cost_model, build_risk_limits, load_config, apply_strategy_overrides
from trading.strategies.mean_reversion import MeanReversionStrategy


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_config_merges_and_expands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if importlib.util.find_spec("yaml") is None:
        pytest.skip("PyYAML not installed")
    base = tmp_path / "base.yaml"
    local = tmp_path / "local.yaml"
    override = tmp_path / "override.yaml"

    _write_yaml(
        base,
        "database:\n  path: ${DB_PATH:default.db}\nbacktest:\n  train_ratio: 0.7\n",
    )
    _write_yaml(local, "backtest:\n  train_ratio: 0.5\n")
    _write_yaml(override, "database:\n  path: override.db\n")

    monkeypatch.setenv("DB_PATH", "env.db")
    cfg = load_config(str(override), base_path=base, local_path=local)

    assert cfg["database"]["path"] == "override.db"
    assert cfg["backtest"]["train_ratio"] == 0.5


def test_build_cost_model_from_bps() -> None:
    cfg = {
        "costs": {
            "base_spread_bps": 200,
            "base_slippage_bps": 50,
            "impact_coefficient": 0.5,
            "max_impact": 0.2,
        }
    }
    model = build_cost_model(cfg)

    assert model.base_spread == pytest.approx(0.02)
    assert model.base_slippage == pytest.approx(0.005)
    assert model.impact_coefficient == pytest.approx(0.5)
    assert model.max_impact == pytest.approx(0.2)


def test_build_risk_limits_from_config() -> None:
    cfg = {
        "risk": {
            "max_position_size": 250,
            "max_positions": 10,
            "max_position_per_market": 2,
            "min_volume_24h": 500,
            "max_spread": 0.08,
        }
    }
    limits = build_risk_limits(cfg)

    assert limits.max_position_size == 250
    assert limits.max_total_positions == 10
    assert limits.max_positions_per_market == 2
    assert limits.min_volume_24h == 500
    assert limits.max_spread_pct == pytest.approx(0.08)


def test_apply_strategy_overrides_updates_parameters() -> None:
    strategy = MeanReversionStrategy()
    overrides = {
        "enabled": False,
        "position_size": 250,
        "shock_threshold": 0.1,
    }
    apply_strategy_overrides(strategy, overrides)

    assert strategy.config.enabled is False
    assert strategy.config.position_size == 250
    assert strategy.config.parameters["shock_threshold"] == pytest.approx(0.1)
