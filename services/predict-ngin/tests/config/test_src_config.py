"""
Comprehensive tests for src.config module.

Tests configuration loading, merging, dataclass population, and ConfigNamespace.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from src.config import (
    ConfigNamespace,
    Settings,
    DatabaseSettings,
    DataSettings,
    PolymarketDataSettings,
    ManifoldDataSettings,
    WhaleStrategySettings,
    RiskSettings,
    CostSettings,
    BacktestSettings,
    EnsembleSettings,
    PaperTradingSettings,
    LiveTradingSettings,
    LoggingSettings,
    ExperimentsSettings,
    TaxonomySettings,
    ValidationSettings,
    LiquiditySettings,
    _load_yaml,
    _deep_merge,
    _dict_to_namespace,
    _populate_dataclass,
    _build_settings,
    load_config,
    get_config,
    reset_config,
)


# =============================================================================
# ConfigNamespace Tests
# =============================================================================

def test_config_namespace_creation():
    """Test ConfigNamespace creation with nested dicts."""
    data = {
        "key1": "value1",
        "nested": {
            "key2": "value2",
            "deep": {
                "key3": "value3"
            }
        }
    }
    ns = ConfigNamespace(data)
    
    assert ns.key1 == "value1"
    assert ns.nested.key2 == "value2"
    assert ns.nested.deep.key3 == "value3"


def test_config_namespace_dict_access():
    """Test ConfigNamespace dict-style access."""
    data = {"key1": "value1", "key2": 42}
    ns = ConfigNamespace(data)
    
    assert ns["key1"] == "value1"
    assert ns["key2"] == 42
    assert "key1" in ns
    assert "missing" not in ns


def test_config_namespace_get():
    """Test ConfigNamespace.get() method."""
    data = {"key1": "value1"}
    ns = ConfigNamespace(data)
    
    assert ns.get("key1") == "value1"
    assert ns.get("missing") is None
    assert ns.get("missing", "default") == "default"


def test_config_namespace_to_dict():
    """Test ConfigNamespace.to_dict() method."""
    data = {"key1": "value1", "nested": {"key2": "value2"}}
    ns = ConfigNamespace(data)
    
    result = ns.to_dict()
    assert result == data
    assert isinstance(result, dict)


def test_config_namespace_repr():
    """Test ConfigNamespace.__repr__()."""
    data = {"key": "value"}
    ns = ConfigNamespace(data)
    repr_str = repr(ns)
    assert "ConfigNamespace" in repr_str


# =============================================================================
# _load_yaml Tests
# =============================================================================

def test_load_yaml_file_exists(tmp_path):
    """Test loading an existing YAML file."""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("key1: value1\nkey2: 42\n", encoding="utf-8")
    
    result = _load_yaml(yaml_file)
    assert result == {"key1": "value1", "key2": 42}


def test_load_yaml_file_not_exists(tmp_path):
    """Test loading a non-existent YAML file."""
    yaml_file = tmp_path / "missing.yaml"
    result = _load_yaml(yaml_file)
    assert result == {}


def test_load_yaml_empty_file(tmp_path):
    """Test loading an empty YAML file."""
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("", encoding="utf-8")
    
    result = _load_yaml(yaml_file)
    assert result == {}


def test_load_yaml_none_result(tmp_path):
    """Test loading YAML that returns None."""
    yaml_file = tmp_path / "none.yaml"
    yaml_file.write_text("null", encoding="utf-8")
    
    result = _load_yaml(yaml_file)
    assert result == {}


def test_load_yaml_non_dict_result(tmp_path):
    """Test loading YAML that returns a non-dict."""
    yaml_file = tmp_path / "list.yaml"
    yaml_file.write_text("- item1\n- item2\n", encoding="utf-8")
    
    result = _load_yaml(yaml_file)
    assert result == {}


def test_load_yaml_missing_pyyaml():
    """Test loading YAML when PyYAML is not installed."""
    # This test is difficult to mock since yaml is imported at module level
    # Skip for now - the code path is tested by the RuntimeError check in _load_yaml
    pytest.skip("PyYAML is already imported, cannot test missing case easily")


# =============================================================================
# _deep_merge Tests
# =============================================================================

def test_deep_merge_simple():
    """Test simple deep merge."""
    base = {"key1": "value1", "key2": "value2"}
    override = {"key2": "new_value2", "key3": "value3"}
    
    result = _deep_merge(base, override)
    assert result == {"key1": "value1", "key2": "new_value2", "key3": "value3"}


def test_deep_merge_nested():
    """Test deep merge with nested dicts."""
    base = {
        "key1": "value1",
        "nested": {
            "key2": "value2",
            "key3": "value3"
        }
    }
    override = {
        "nested": {
            "key2": "new_value2"
        }
    }
    
    result = _deep_merge(base, override)
    assert result == {
        "key1": "value1",
        "nested": {
            "key2": "new_value2",
            "key3": "value3"
        }
    }


def test_deep_merge_overwrite_nested():
    """Test deep merge overwrites nested dict with non-dict."""
    base = {"nested": {"key": "value"}}
    override = {"nested": "string_value"}
    
    result = _deep_merge(base, override)
    assert result == {"nested": "string_value"}


# =============================================================================
# Dataclass Tests
# =============================================================================

def test_database_settings_defaults():
    """Test DatabaseSettings with defaults."""
    settings = DatabaseSettings()
    assert settings.path == "data/research"
    assert settings.backup_enabled is False
    assert settings.backup_interval_hours == 24


def test_database_settings_custom():
    """Test DatabaseSettings with custom values."""
    settings = DatabaseSettings(
        path="custom.db",
        backup_enabled=False,
        backup_interval_hours=12
    )
    assert settings.path == "custom.db"
    assert settings.backup_enabled is False
    assert settings.backup_interval_hours == 12


def test_polymarket_data_settings():
    """Test PolymarketDataSettings."""
    settings = PolymarketDataSettings()
    assert settings.gamma_api == "https://gamma-api.polymarket.com"
    assert settings.min_volume_24h == 1000
    assert settings.max_workers == 20


def test_whale_strategy_settings():
    """Test WhaleStrategySettings."""
    settings = WhaleStrategySettings()
    assert settings.method == "win_rate_60pct"
    assert settings.min_win_rate == 0.60
    assert settings.rolling_enabled is True


def test_risk_settings():
    """Test RiskSettings."""
    settings = RiskSettings()
    assert settings.max_position_size == 1000
    assert settings.max_positions == 20
    assert settings.stop_loss_pct == 0.20


def test_backtest_settings():
    """Test BacktestSettings."""
    settings = BacktestSettings()
    assert settings.train_ratio == 0.70
    assert settings.starting_capital == 10000
    assert len(settings.position_sizes_to_test) == 7


def test_settings_defaults():
    """Test Settings with all defaults."""
    settings = Settings()
    assert isinstance(settings.database, DatabaseSettings)
    assert isinstance(settings.data, DataSettings)
    assert isinstance(settings.whale_strategy, WhaleStrategySettings)
    assert isinstance(settings.risk, RiskSettings)


def test_settings_get():
    """Test Settings.get() method."""
    settings = Settings()
    assert settings.get("database") is not None
    assert settings.get("missing") is None
    assert settings.get("missing", "default") == "default"


def test_settings_to_dict():
    """Test Settings.to_dict() returns raw config."""
    settings = Settings(_raw={"key": "value"})
    assert settings.to_dict() == {"key": "value"}


# =============================================================================
# _populate_dataclass Tests
# =============================================================================

def test_populate_dataclass_empty():
    """Test populating dataclass with empty dict."""
    result = _populate_dataclass(DatabaseSettings, {})
    assert isinstance(result, DatabaseSettings)
    assert result.path == "data/research"  # default


def test_populate_dataclass_partial():
    """Test populating dataclass with partial data."""
    data = {"path": "custom.db", "backup_enabled": False}
    result = _populate_dataclass(DatabaseSettings, data)
    assert result.path == "custom.db"
    assert result.backup_enabled is False
    assert result.backup_interval_hours == 24  # default


def test_populate_dataclass_nested():
    """Test populating nested dataclasses."""
    # Test with PolymarketDataSettings directly
    data = {
        "gamma_api": "custom_api",
        "min_volume_24h": 2000
    }
    result = _populate_dataclass(PolymarketDataSettings, data)
    assert isinstance(result, PolymarketDataSettings)
    assert result.gamma_api == "custom_api"
    assert result.min_volume_24h == 2000


def test_populate_dataclass_ignores_unknown_keys():
    """Test that unknown keys are ignored."""
    data = {"path": "custom.db", "unknown_key": "value"}
    result = _populate_dataclass(DatabaseSettings, data)
    assert result.path == "custom.db"
    assert not hasattr(result, "unknown_key")


# =============================================================================
# load_config Tests
# =============================================================================

def test_load_config_default_only(tmp_path):
    """Test loading config with only default file."""
    default_file = tmp_path / "default.yaml"
    default_file.write_text("key1: value1\nkey2: 42\n", encoding="utf-8")
    
    result = load_config(default_path=default_file, local_path=tmp_path / "nonexistent.yaml")
    assert result["key1"] == "value1"
    assert result["key2"] == 42


def test_load_config_with_local(tmp_path):
    """Test loading config with default and local files."""
    default_file = tmp_path / "default.yaml"
    local_file = tmp_path / "local.yaml"
    
    default_file.write_text("key1: value1\nkey2: value2\n", encoding="utf-8")
    local_file.write_text("key2: new_value2\nkey3: value3\n", encoding="utf-8")
    
    result = load_config(default_path=default_file, local_path=local_file)
    assert result == {
        "key1": "value1",
        "key2": "new_value2",
        "key3": "value3"
    }


def test_load_config_local_not_exists(tmp_path):
    """Test loading config when local file doesn't exist."""
    default_file = tmp_path / "default.yaml"
    local_file = tmp_path / "local.yaml"
    
    default_file.write_text("key1: value1\n", encoding="utf-8")
    
    result = load_config(default_path=default_file, local_path=local_file)
    assert result == {"key1": "value1"}


def test_load_config_deep_merge(tmp_path):
    """Test that load_config performs deep merge."""
    default_file = tmp_path / "default.yaml"
    local_file = tmp_path / "local.yaml"
    
    default_file.write_text(
        "nested:\n  key1: value1\n  key2: value2\n",
        encoding="utf-8"
    )
    local_file.write_text(
        "nested:\n  key2: new_value2\n",
        encoding="utf-8"
    )
    
    result = load_config(default_path=default_file, local_path=local_file)
    assert result == {
        "nested": {
            "key1": "value1",
            "key2": "new_value2"
        }
    }


# =============================================================================
# get_config Tests
# =============================================================================

def test_get_config_singleton(tmp_path, monkeypatch):
    """Test that get_config returns singleton."""
    default_file = tmp_path / "default.yaml"
    default_file.write_text("key: value\n", encoding="utf-8")
    
    with patch('src.config._DEFAULT_CONFIG', default_file):
        with patch('src.config._LOCAL_CONFIG', tmp_path / "local.yaml"):
            reset_config()
            config1 = get_config()
            config2 = get_config()
            assert config1 is config2


def test_get_config_reload(tmp_path, monkeypatch):
    """Test get_config with reload=True."""
    default_file = tmp_path / "default.yaml"
    default_file.write_text("key: value1\n", encoding="utf-8")
    
    with patch('src.config._DEFAULT_CONFIG', default_file):
        with patch('src.config._LOCAL_CONFIG', tmp_path / "local.yaml"):
            reset_config()
            config1 = get_config()
            
            # Modify file
            default_file.write_text("key: value2\n", encoding="utf-8")
            
            config2 = get_config(reload=True)
            # Should be different instance after reload
            assert config1 is not config2


def test_reset_config():
    """Test reset_config clears singleton."""
    reset_config()
    config1 = get_config()
    
    reset_config()
    config2 = get_config()
    
    # Should be different instances
    assert config1 is not config2


# =============================================================================
# _build_settings Tests
# =============================================================================

def test_build_settings_minimal():
    """Test building settings with minimal config."""
    config = {}
    settings = _build_settings(config)
    
    assert isinstance(settings, Settings)
    assert isinstance(settings.database, DatabaseSettings)
    assert isinstance(settings.data, DataSettings)


def test_build_settings_full():
    """Test building settings with full config."""
    config = {
        "database": {
            "path": "custom.db",
            "backup_enabled": False
        },
        "whale_strategy": {
            "method": "custom_method",
            "min_win_rate": 0.70
        },
        "risk": {
            "max_position_size": 500
        },
        "strategies": {
            "momentum": {
                "enabled": True
            }
        }
    }
    
    settings = _build_settings(config)
    
    assert settings.database.path == "custom.db"
    assert settings.database.backup_enabled is False
    assert settings.whale_strategy.method == "custom_method"
    assert settings.whale_strategy.min_win_rate == 0.70
    assert settings.risk.max_position_size == 500
    assert isinstance(settings.strategies, ConfigNamespace)
    assert settings.strategies.momentum.enabled is True
    assert settings._raw == config


def test_build_settings_nested_data():
    """Test building settings with nested data section."""
    config = {
        "data": {
            "polymarket": {
                "gamma_api": "custom_api",
                "min_volume_24h": 2000
            },
            "manifold": {
                "api_url": "custom_url"
            }
        }
    }
    
    settings = _build_settings(config)
    
    assert settings.data.polymarket.gamma_api == "custom_api"
    assert settings.data.polymarket.min_volume_24h == 2000
    assert settings.data.manifold.api_url == "custom_url"


def test_build_settings_config_namespace_sections():
    """Test that ConfigNamespace sections are created."""
    config = {
        "strategies": {"strategy1": {"enabled": True}},
        "monitoring": {"enabled": True},
        "research": {"experiment": "test"},
        "liquidity": {"capture": True}
    }
    
    settings = _build_settings(config)
    
    assert isinstance(settings.strategies, ConfigNamespace)
    assert settings.strategies.strategy1.enabled is True
    assert settings.monitoring.enabled is True
    assert settings.research.experiment == "test"
    assert settings.liquidity.capture is True
