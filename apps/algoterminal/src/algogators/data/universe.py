"""Named baskets of instruments ("universes") that can be referenced by name
anywhere in the tool instead of re-typing ticker lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from algogators.config import UNIVERSE_DIR, ensure_dirs
from algogators.data.provider import AssetClass


@dataclass
class Universe:
    name: str
    asset_class: AssetClass
    symbols: list[str]
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "asset_class": self.asset_class.value,
            "symbols": self.symbols,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Universe":
        return cls(
            name=data["name"],
            asset_class=AssetClass(data["asset_class"]),
            symbols=list(data["symbols"]),
            description=data.get("description", ""),
        )


# Shipped so the tool is useful out of the box without any setup.
_BUILTIN_UNIVERSES: list[Universe] = [
    Universe(
        name="g10-fx",
        asset_class=AssetClass.FX,
        symbols=["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X", "USDCHF=X", "NZDUSD=X"],
        description="G10 currency pairs vs. USD",
    ),
    Universe(
        name="spx-tech",
        asset_class=AssetClass.EQUITY,
        symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "AVGO"],
        description="Large-cap S&P 500 technology names",
    ),
    Universe(
        name="crypto-majors",
        asset_class=AssetClass.CRYPTO,
        symbols=["BTC-USD", "ETH-USD", "SOL-USD"],
        description="Major crypto assets vs. USD",
    ),
    Universe(
        name="us-rates-futures",
        asset_class=AssetClass.FUTURE,
        symbols=["ZN=F", "ZB=F", "ZT=F"],
        description="US Treasury futures (10Y, 30Y, 2Y)",
    ),
]


class UniverseStore:
    """Reads/writes universe YAML files under ~/.algogators/universes."""

    def __init__(self) -> None:
        ensure_dirs()
        self._seed_builtins()

    def _seed_builtins(self) -> None:
        for u in _BUILTIN_UNIVERSES:
            path = self._path(u.name)
            if not path.exists():
                self.save(u)

    @staticmethod
    def _path(name: str):
        return UNIVERSE_DIR / f"{name}.yaml"

    def save(self, universe: Universe) -> None:
        with open(self._path(universe.name), "w", encoding="utf-8") as f:
            yaml.safe_dump(universe.to_dict(), f, sort_keys=False)

    def load(self, name: str) -> Universe:
        path = self._path(name)
        if not path.exists():
            raise KeyError(f"no such universe: {name!r}")
        with open(path, encoding="utf-8") as f:
            return Universe.from_dict(yaml.safe_load(f))

    def delete(self, name: str) -> None:
        path = self._path(name)
        if path.exists():
            path.unlink()

    def list(self) -> list[Universe]:
        return [self.load(p.stem) for p in sorted(UNIVERSE_DIR.glob("*.yaml"))]

    def resolve(self, name_or_symbols: str) -> list[str]:
        """Resolve a universe name to its symbols, or treat the input as a
        comma-separated symbol list if it isn't a known universe.
        """
        try:
            return self.load(name_or_symbols).symbols
        except KeyError:
            return [s.strip() for s in name_or_symbols.split(",") if s.strip()]
