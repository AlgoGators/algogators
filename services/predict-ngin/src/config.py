"""
Configuration management for predict-ngin.

LEGACY (2026-10-15, docs/ARCHITECTURE.md D14): the Feb 2026 global config for
the whale/backtest stack (src/trading, src/whale_strategy, src/backtest). Its
consumers are legacy scripts (scripts/backtest, scripts/monitoring/dashboard.py)
and tests/config/test_src_config.py. Current code configures itself per job
(config/collectors.yaml, config/recorder.yaml, PREDICT_NGIN_DATA). Kept for
reference; not maintained. predict_ngin must not import it (.importlinter).

Loads config/default.yaml as base, merges config/local.yaml if exists,
and provides typed access via dataclasses.

Usage:
    from src.config import get_config
    config = get_config()
    print(config.whale_strategy.method)
    print(config.risk.max_position_size)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None


# Config file paths relative to project root
_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_CONFIG = _PROJECT_ROOT / "config" / "default.yaml"
_LOCAL_CONFIG = _PROJECT_ROOT / "config" / "local.yaml"

# Singleton instance
_config_instance: Optional[Settings] = None


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file, returning empty dict if not found."""
    if not path.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML required. Install with: pip install PyYAML")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _dict_to_namespace(d: Dict[str, Any]) -> ConfigNamespace:
    """Convert a dict to a ConfigNamespace for dot-access."""
    return ConfigNamespace(d)


class ConfigNamespace:
    """
    A namespace object that allows dot-access to nested config values.

    Supports both attribute access (config.key) and dict-style access (config["key"]).
    """

    def __init__(self, data: Dict[str, Any]):
        for key, value in data.items():
            if isinstance(value, dict):
                setattr(self, key, ConfigNamespace(value))
            else:
                setattr(self, key, value)
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert back to a plain dict."""
        return self._data

    def __repr__(self) -> str:
        return f"ConfigNamespace({self._data})"


# =============================================================================
# TYPED SETTINGS DATACLASSES
# =============================================================================

@dataclass
class DatabaseSettings:
    path: str = "data/research"  # Legacy; DB deprecated
    polymarket_trades_path: str = "data/research"
    parquet_dir: str = "data/research"
    backup_enabled: bool = False
    backup_interval_hours: int = 24


@dataclass
class PolymarketDataSettings:
    gamma_api: str = "https://gamma-api.polymarket.com"
    clob_api: str = "https://clob.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    data_dir: str = "data/research"
    min_volume_24h: int = 1000
    max_markets: int = 500
    request_delay: float = 0.1
    max_workers: int = 20


@dataclass
class ManifoldDataSettings:
    api_url: str = "https://api.manifold.markets/v0"
    request_delay: float = 0.1
    data_dir: str = "data/manifold"


@dataclass
class DataSettings:
    polymarket: PolymarketDataSettings = field(default_factory=PolymarketDataSettings)
    manifold: ManifoldDataSettings = field(default_factory=ManifoldDataSettings)


@dataclass
class WhaleStrategySettings:
    method: str = "win_rate_60pct"
    min_trades: int = 20
    min_volume: int = 1000
    min_extreme_trades: int = 10
    min_win_rate: float = 0.60
    rolling_enabled: bool = True
    lookback_months: int = 3
    min_signal_usd: int = 100


@dataclass
class RiskSettings:
    max_position_size: int = 1000
    max_total_exposure: int = 10000
    max_positions: int = 20
    max_position_per_market: int = 1
    max_daily_drawdown: float = 0.05
    max_weekly_drawdown: float = 0.10
    max_total_drawdown: float = 0.20
    max_holding_hours: int = 48
    stop_loss_pct: float = 0.20
    take_profit_pct: float = 0.30
    min_liquidity: int = 500
    min_volume_24h: int = 1000
    max_spread: float = 0.10


@dataclass
class CostSettings:
    base_spread_bps: int = 200
    base_slippage_bps: int = 50
    impact_coefficient: float = 0.5
    fee_rate_bps: int = 10


@dataclass
class BacktestSettings:
    train_ratio: float = 0.70
    test_days: Optional[int] = None
    test_months: Optional[int] = None
    test_start: Optional[str] = None
    test_end: Optional[str] = None
    starting_capital: int = 10000
    include_open: bool = True
    enforce_one_position_per_market: bool = True
    min_liquidity: int = 0
    min_volume: int = 0
    fill_latency_seconds: int = 60
    max_holding_seconds: int = 172800
    default_position_size: int = 250
    position_sizes_to_test: List[int] = field(default_factory=lambda: [100, 250, 500, 1000, 2500, 5000, 10000])
    output_dir: str = "data/research"
    generate_html_report: bool = True


@dataclass
class EnsembleSettings:
    enabled: bool = True
    method: str = "sharpe_weighted"
    weights: Dict[str, float] = field(default_factory=lambda: {
        "whale_following": 0.40,
        "smart_money": 0.20,
        "momentum": 0.15,
        "mean_reversion": 0.10,
        "breakout": 0.10,
        "time_decay": 0.05,
    })


@dataclass
class PaperTradingSettings:
    initial_capital: int = 10000
    state_file: str = "data/paper_trading_state.json"
    log_file: str = "data/paper_trading_log.jsonl"
    max_position_size: int = 500
    max_positions: int = 10
    check_interval_seconds: int = 1
    price_update_interval_seconds: int = 10


@dataclass
class LiveTradingSettings:
    enabled: bool = False
    dry_run: bool = True
    max_position_size: int = 100
    max_total_exposure: int = 500
    max_positions: int = 5
    order_type: str = "market"
    slippage_tolerance: float = 0.02


@dataclass
class LoggingSettings:
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "path": "logs/predict-ngin.log",
        "max_bytes": 10485760,
        "backup_count": 5,
    })
    execution: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "path": "data/execution_log.jsonl",
        "db_path": "data/execution_metrics.db",
    })


@dataclass
class ExperimentsSettings:
    """Settings for experiment tracking."""
    enabled: bool = True
    dir: str = "research/experiments"
    auto_track: bool = True
    git_commit: bool = True


@dataclass
class TaxonomySettings:
    """Settings for market taxonomy."""
    path: str = "config/taxonomy.yaml"
    method: str = "hybrid"  # keyword, ml, hybrid
    cache_classifications: bool = True


@dataclass
class ValidationSettings:
    """Settings for data validation."""
    run_before_backtest: bool = True
    fail_on_error: bool = True
    checks: List[str] = field(default_factory=lambda: [
        "completeness",
        "date_gaps",
        "duplicates",
        "outliers",
    ])


@dataclass
class LiquiditySettings:
    """Settings for liquidity collection."""
    capture_interval_minutes: int = 5
    min_volume: float = 1000
    max_markets: int = 500
    store_dir: str = "data/polymarket/liquidity"


@dataclass
class Settings:
    """
    Main settings class with typed access to all configuration sections.

    Example:
        config = get_config()
        print(config.whale_strategy.method)
        print(config.risk.max_position_size)
    """
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    data: DataSettings = field(default_factory=DataSettings)
    whale_strategy: WhaleStrategySettings = field(default_factory=WhaleStrategySettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    costs: CostSettings = field(default_factory=CostSettings)
    backtest: BacktestSettings = field(default_factory=BacktestSettings)
    ensemble: EnsembleSettings = field(default_factory=EnsembleSettings)
    paper_trading: PaperTradingSettings = field(default_factory=PaperTradingSettings)
    live_trading: LiveTradingSettings = field(default_factory=LiveTradingSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)

    # New research pipeline settings
    experiments: ExperimentsSettings = field(default_factory=ExperimentsSettings)
    taxonomy: TaxonomySettings = field(default_factory=TaxonomySettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    liquidity_capture: LiquiditySettings = field(default_factory=LiquiditySettings)

    # For sections not covered by typed dataclasses, use ConfigNamespace
    strategies: ConfigNamespace = field(default_factory=lambda: ConfigNamespace({}))
    monitoring: ConfigNamespace = field(default_factory=lambda: ConfigNamespace({}))
    research: ConfigNamespace = field(default_factory=lambda: ConfigNamespace({}))
    liquidity: ConfigNamespace = field(default_factory=lambda: ConfigNamespace({}))

    # Raw config dict for advanced access
    _raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a top-level config value."""
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Return the raw configuration dict."""
        return self._raw


def _populate_dataclass(dc_class: type, data: Dict[str, Any]) -> Any:
    """Create a dataclass instance from a dict, handling nested dataclasses."""
    if not data:
        return dc_class()

    field_types = {f.name: f.type for f in dc_class.__dataclass_fields__.values()}
    kwargs = {}

    for key, value in data.items():
        if key not in field_types:
            continue

        field_type = field_types[key]

        # Handle nested dataclasses
        if isinstance(value, dict):
            # Check if the type annotation is a dataclass
            type_str = str(field_type)
            if "Settings" in type_str and hasattr(field_type, "__dataclass_fields__"):
                kwargs[key] = _populate_dataclass(field_type, value)
            else:
                kwargs[key] = value
        else:
            kwargs[key] = value

    return dc_class(**kwargs)


def _build_settings(config: Dict[str, Any]) -> Settings:
    """Build a Settings instance from a config dict."""

    # Database
    database = _populate_dataclass(DatabaseSettings, config.get("database", {}))

    # Data (with nested settings)
    data_config = config.get("data", {})
    data = DataSettings(
        polymarket=_populate_dataclass(PolymarketDataSettings, data_config.get("polymarket", {})),
        manifold=_populate_dataclass(ManifoldDataSettings, data_config.get("manifold", {})),
    )

    # Simple sections
    whale_strategy = _populate_dataclass(WhaleStrategySettings, config.get("whale_strategy", {}))
    risk = _populate_dataclass(RiskSettings, config.get("risk", {}))
    costs = _populate_dataclass(CostSettings, config.get("costs", {}))
    backtest = _populate_dataclass(BacktestSettings, config.get("backtest", {}))
    ensemble = _populate_dataclass(EnsembleSettings, config.get("ensemble", {}))
    paper_trading = _populate_dataclass(PaperTradingSettings, config.get("paper_trading", {}))
    live_trading = _populate_dataclass(LiveTradingSettings, config.get("live_trading", {}))
    logging_settings = _populate_dataclass(LoggingSettings, config.get("logging", {}))

    # New research pipeline settings
    experiments = _populate_dataclass(ExperimentsSettings, config.get("experiments", {}))
    taxonomy = _populate_dataclass(TaxonomySettings, config.get("taxonomy", {}))
    validation = _populate_dataclass(ValidationSettings, config.get("validation", {}))
    liquidity_capture = _populate_dataclass(LiquiditySettings, config.get("liquidity", {}))

    # ConfigNamespace sections for flexibility
    strategies = ConfigNamespace(config.get("strategies", {}))
    monitoring = ConfigNamespace(config.get("monitoring", {}))
    research = ConfigNamespace(config.get("research", {}))
    liquidity = ConfigNamespace(config.get("liquidity", {}))

    return Settings(
        database=database,
        data=data,
        whale_strategy=whale_strategy,
        risk=risk,
        costs=costs,
        backtest=backtest,
        ensemble=ensemble,
        paper_trading=paper_trading,
        live_trading=live_trading,
        logging=logging_settings,
        experiments=experiments,
        taxonomy=taxonomy,
        validation=validation,
        liquidity_capture=liquidity_capture,
        strategies=strategies,
        monitoring=monitoring,
        research=research,
        liquidity=liquidity,
        _raw=config,
    )


def load_config(
    default_path: Optional[Path] = None,
    local_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Load and merge configuration files.

    Args:
        default_path: Path to default config (defaults to config/default.yaml)
        local_path: Path to local overrides (defaults to config/local.yaml)

    Returns:
        Merged configuration dict
    """
    default_path = default_path or _DEFAULT_CONFIG
    local_path = local_path or _LOCAL_CONFIG

    config = _load_yaml(default_path)

    if local_path.exists():
        local_config = _load_yaml(local_path)
        config = _deep_merge(config, local_config)

    return config


def get_config(reload: bool = False) -> Settings:
    """
    Get the configuration singleton.

    Args:
        reload: If True, reload config from files instead of using cached instance

    Returns:
        Settings instance with typed access to configuration

    Example:
        config = get_config()
        print(config.whale_strategy.method)  # "win_rate_60pct"
        print(config.risk.max_position_size)  # 1000
    """
    global _config_instance

    if _config_instance is None or reload:
        raw_config = load_config()
        _config_instance = _build_settings(raw_config)

    return _config_instance


def reset_config() -> None:
    """Reset the configuration singleton (useful for testing)."""
    global _config_instance
    _config_instance = None


__all__ = [
    "get_config",
    "load_config",
    "reset_config",
    "Settings",
    "ConfigNamespace",
    "DatabaseSettings",
    "DataSettings",
    "WhaleStrategySettings",
    "RiskSettings",
    "CostSettings",
    "BacktestSettings",
    "EnsembleSettings",
    "PaperTradingSettings",
    "LiveTradingSettings",
    "LoggingSettings",
    "ExperimentsSettings",
    "TaxonomySettings",
    "ValidationSettings",
    "LiquiditySettings",
]
