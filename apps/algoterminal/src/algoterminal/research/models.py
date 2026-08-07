"""The structured hypothesis object at the center of the research cycle."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from algoterminal.data.provider import AssetClass


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "hypothesis"


@dataclass
class Hypothesis:
    title: str
    thesis: str
    universe: str
    symbols: list[str]
    expected_edge: str
    asset_class: AssetClass = AssetClass.EQUITY
    risk_notes: str = ""
    author: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def slug(self) -> str:
        return slugify(self.title)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "thesis": self.thesis,
            "universe": self.universe,
            "symbols": self.symbols,
            "expected_edge": self.expected_edge,
            "asset_class": self.asset_class.value,
            "risk_notes": self.risk_notes,
            "author": self.author,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Hypothesis":
        return cls(
            title=data["title"],
            thesis=data["thesis"],
            universe=data["universe"],
            symbols=list(data["symbols"]),
            expected_edge=data["expected_edge"],
            asset_class=AssetClass(data.get("asset_class", AssetClass.EQUITY.value)),
            risk_notes=data.get("risk_notes", ""),
            author=data.get("author", ""),
            created_at=data.get("created_at", ""),
        )
