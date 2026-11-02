"""Shared fixtures for the algoterminal test suite.

Every test runs against a temporary ~/.algoterminal replacement (no test
ever touches the real home directory) and with outbound HTTP blocked (no
test ever touches the network). Provider tests stub `requests` / `yfinance`
explicitly on top of that guard.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
import requests
from algoterminal.data.provider import AssetClass, DataProvider
from algoterminal.research.models import Hypothesis

# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Redirect all on-disk state to a per-test temporary directory.

    `config` computes its paths at import time and each consuming module
    binds them into its own namespace with `from config import ...`, so the
    per-module copies have to be re-pointed as well.
    """
    import algoterminal.config as config
    from algoterminal.data import cache as cache_mod
    from algoterminal.data import metadata as metadata_mod
    from algoterminal.data import universe as universe_mod
    from algoterminal.research import storage as storage_mod

    home = tmp_path / "algoterminal-home"
    monkeypatch.setattr(config, "HOME_DIR", home)
    monkeypatch.setattr(config, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(config, "UNIVERSE_DIR", home / "universes")
    monkeypatch.setattr(config, "RESEARCH_DIR", home / "research")
    monkeypatch.setattr(cache_mod, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(universe_mod, "UNIVERSE_DIR", home / "universes")
    monkeypatch.setattr(metadata_mod, "_METADATA_PATH", home / "instrument_metadata.json")
    monkeypatch.setattr(storage_mod, "RESEARCH_DIR", home / "research")
    return home


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if any test lets a real HTTP request through."""

    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted during tests")

    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests, "post", _blocked)
    monkeypatch.setattr(requests.Session, "request", _blocked)


# ---------------------------------------------------------------------------
# Synthetic data factories
# ---------------------------------------------------------------------------


def make_close_series(
    n: int = 250, start: str = "2024-01-02", seed: int = 7, drift: float = 0.0005
) -> pd.Series:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(start, periods=n, name="date")
    returns = rng.normal(drift, 0.01, n)
    close = 100.0 * np.cumprod(1 + returns)
    return pd.Series(close, index=index, name="close")


def make_ohlcv(n: int = 250, start: str = "2024-01-02", seed: int = 7) -> pd.DataFrame:
    close = make_close_series(n=n, start=start, seed=seed)
    frame = pd.DataFrame(
        {
            "open": close.values * 0.999,
            "high": close.values * 1.01,
            "low": close.values * 0.99,
            "close": close.values,
            "volume": np.full(n, 1_000.0),
        },
        index=close.index,
    )
    frame.index.name = "date"
    return frame


class FakeProvider(DataProvider):
    """Deterministic offline DataProvider for tests."""

    name = "fake"

    def __init__(
        self,
        frames: dict[str, pd.DataFrame] | None = None,
        n: int = 250,
        fail: tuple[str, ...] = (),
    ) -> None:
        self.frames = frames or {}
        self.n = n
        self.fail = fail
        self.calls: list[str] = []

    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        self.calls.append(symbol)
        if symbol in self.fail:
            raise RuntimeError(f"boom: {symbol}")
        if symbol in self.frames:
            return self.frames[symbol]
        seed = sum(ord(c) for c in symbol)
        return make_ohlcv(n=self.n, seed=seed)


def make_hypothesis(
    title: str = "Test Momentum",
    symbols: list[str] | None = None,
    universe: str = "test-uni",
) -> Hypothesis:
    return Hypothesis(
        title=title,
        thesis="Momentum persists.",
        universe=universe,
        symbols=symbols or ["AAA"],
        expected_edge="Behavioral underreaction.",
        asset_class=AssetClass.EQUITY,
        risk_notes="Single name.",
        author="tests",
    )


SMA_STRATEGY = '''"""SMA crossover strategy used by the test-suite."""

from __future__ import annotations

import pandas as pd

FAST, SLOW = 5, 20


def generate_signals(prices: pd.Series) -> pd.Series:
    fast = prices.rolling(FAST).mean()
    slow = prices.rolling(SLOW).mean()
    signal = (fast > slow).astype(float) - (fast < slow).astype(float)
    return signal.fillna(0.0)


def size_positions(signal: pd.Series, prices: pd.Series) -> pd.Series:
    return signal.astype(float)


def apply_risk_rules(positions: pd.Series, prices: pd.Series) -> pd.Series:
    return positions.clip(-1.0, 1.0)
'''


class StubResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, text: str = "", json_data=None, status_code: int = 200) -> None:
        self.text = text
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json
