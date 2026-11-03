"""The event catalogue (the fund's black-swan events package), read as data.

Each event folder holds `_index.json` (the instruments the event is about), and either
`measured_window.csv` (a multi-session window) or `measured_singleday.csv` (one session).
Only the window dates, the variant and the named instruments are taken from it. Every move
is recomputed from our own prices (see scenario.py), and `selfcheck` in the CLI confirms
the recomputation reproduces the package's measured numbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# The catalogue names the full-size equity index contracts; we trade the micros.
CATALOGUE_TO_DB = {"ES": "MES", "NQ": "MNQ", "RTY": "M2K", "YM": "MYM"}


def db_root(catalogue_symbol: str) -> str:
    return CATALOGUE_TO_DB.get(catalogue_symbol, catalogue_symbol)


@dataclass(frozen=True)
class Event:
    id: str
    name: str
    klass: str
    variant: str  # "" unless the event carries several variants
    start: pd.Timestamp  # window start, or the event day for single-session events
    end: pd.Timestamp
    single: bool
    instruments: tuple[str, ...]  # DB roots the catalogue names for this event
    measured: pd.DataFrame  # the package's own measurement rows, indexed by DB root


def pick_variant(measured: pd.DataFrame, single: bool) -> pd.DataFrame:
    """Two events carry two variants each (MAJ-02, MAJ-09). The catalogue leads with the
    worst: the variant with the largest mean absolute excursion over the named instruments,
    as the package's own generator chooses (engine/synth_ab.py, pick_variant)."""
    if "variant" not in measured.columns:
        return measured
    variants = [v for v in measured["variant"].dropna().unique() if str(v).strip()]
    if len(variants) < 2:
        return measured
    col = "move" if single else "mdd"
    score = {v: measured.loc[measured["variant"] == v, col].abs().mean() for v in variants}
    worst = max(score, key=lambda v: score[v])
    return measured[measured["variant"] == worst]


def load_event(folder: Path, manifest: pd.DataFrame) -> Event:
    eid = folder.name
    index = json.loads((folder / "_index.json").read_text())
    single = (folder / "measured_singleday.csv").exists() and not (
        folder / "measured_window.csv"
    ).exists()
    measured = pd.read_csv(folder / ("measured_singleday.csv" if single else "measured_window.csv"))
    measured = measured[measured.sym.isin(index["instruments"])]
    measured = pick_variant(measured, single)
    if measured.empty:
        raise ValueError(f"{eid}: no measured rows for its named instruments")
    variant = measured["variant"].iloc[0] if "variant" in measured.columns else ""
    measured = measured.assign(root=measured.sym.map(db_root)).set_index("root")
    row = manifest.loc[eid]
    return Event(
        id=eid,
        name=str(row["name"]),
        klass=str(row["klass"]),
        variant="" if pd.isna(variant) else str(variant),
        start=pd.Timestamp(measured["a"].iloc[0]),
        end=pd.Timestamp(measured["b"].iloc[0]),
        single=single,
        instruments=tuple(dict.fromkeys(measured.index)),
        measured=measured,
    )


def load_events(package: Path) -> list[Event]:
    """`package` is the events-package folder (holding MANIFEST.csv and events/)."""
    manifest = pd.read_csv(package / "MANIFEST.csv").set_index("id")
    folders = sorted(p for p in (package / "events").iterdir() if (p / "_index.json").exists())
    return [load_event(f, manifest) for f in folders]
