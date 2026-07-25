"""Versioned, on-disk storage for research records.

Every run of the hypothesis -> data -> methodology -> backtest -> writeup
cycle lives in its own timestamped directory under
~/.algogators/research/<slug>/<version>/, so a strategy's iteration
history is fully browsable later:

    ~/.algogators/research/momentum-fx-carry/
        20260401-101500/
            hypothesis.yaml
            data_quality.yaml
            strategy.py
            backtest_results.json
            equity_curve.parquet
            writeup.md
        20260403-091200/
            ...
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from algogators.config import RESEARCH_DIR, ensure_dirs
from algogators.research.models import Hypothesis


@dataclass
class ResearchRecord:
    slug: str
    version: str
    path: Path

    @property
    def hypothesis_path(self) -> Path:
        return self.path / "hypothesis.yaml"

    @property
    def strategy_path(self) -> Path:
        return self.path / "strategy.py"

    @property
    def data_quality_path(self) -> Path:
        return self.path / "data_quality.yaml"

    @property
    def backtest_results_path(self) -> Path:
        return self.path / "backtest_results.json"

    @property
    def equity_curve_path(self) -> Path:
        return self.path / "equity_curve.parquet"

    @property
    def writeup_path(self) -> Path:
        return self.path / "writeup.md"

    def load_hypothesis(self) -> Hypothesis:
        with open(self.hypothesis_path, encoding="utf-8") as f:
            return Hypothesis.from_dict(yaml.safe_load(f))


def create_record(hypothesis: Hypothesis) -> ResearchRecord:
    """Start a new version of a hypothesis's research record and persist it."""
    ensure_dirs()
    slug = hypothesis.slug
    version = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESEARCH_DIR / slug / version
    path.mkdir(parents=True, exist_ok=True)

    with open(path / "hypothesis.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(hypothesis.to_dict(), f, sort_keys=False)

    return ResearchRecord(slug=slug, version=version, path=path)


def list_slugs() -> list[str]:
    ensure_dirs()
    return sorted(p.name for p in RESEARCH_DIR.iterdir() if p.is_dir())


def list_versions(slug: str) -> list[ResearchRecord]:
    slug_dir = RESEARCH_DIR / slug
    if not slug_dir.exists():
        return []
    return [
        ResearchRecord(slug=slug, version=p.name, path=p)
        for p in sorted(slug_dir.iterdir())
        if p.is_dir()
    ]


def latest_record(slug: str) -> ResearchRecord | None:
    versions = list_versions(slug)
    return versions[-1] if versions else None


def get_record(slug: str, version: str) -> ResearchRecord:
    path = RESEARCH_DIR / slug / version
    if not path.exists():
        raise KeyError(f"no such research record: {slug}/{version}")
    return ResearchRecord(slug=slug, version=version, path=path)
