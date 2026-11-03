"""Tier 1: an event's moves applied to a book held unchanged.

P&L of instrument i at session t = contracts_i * notional per contract_i * level_i(t), where
notional is at the last close before the snapshot day. Nothing trades during the event, so
for a multi-week event this is the "held through it" figure, a bound rather than what the
strategy would do: the package found static exposure explains only part of the dynamic
impact (CORRECTIONS.md #2). For single-session events nothing could trade mid-move, so it
is close to the real answer.

Three scopes, each moving held instruments by their own real history over the window:
- "named": only the instruments the catalogue names for the event (its definition of the
  event, and what the package's generator perturbs).
- "sector": the named instruments plus any held instrument in the same sector. The
  catalogue measured 28 markets; our books also hold ZT, ZF, 6A, 6L, which it never
  measured, so under "named" a Treasury event would leave the 2-year short untouched.
- "all": every held instrument (a straight historical replay of the cross-section; over a
  long window this includes unrelated drift).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from .catalogue import Event
from .scenario import Move, event_moves

SCOPES = ("named", "sector", "all")


def in_scope(
    held: list[str], ev: Event, scope: str, sectors: Mapping[str, str] | None = None
) -> list[str]:
    if scope == "all":
        return held
    if scope == "named":
        return [r for r in held if r in ev.instruments]
    if scope == "sector":
        sectors = sectors or {}
        hit = {sectors[n] for n in ev.instruments if n in sectors}
        return [r for r in held if r in ev.instruments or sectors.get(r) in hit]
    raise ValueError(f"scope must be one of {SCOPES}")


@dataclass(frozen=True)
class Shock:
    pnl: pd.DataFrame  # index: event sessions; columns: held roots that moved; dollars
    uncovered: tuple[str, ...]  # held roots that should move in this scope but have no data
    moves: Mapping[str, Move]

    @property
    def total(self) -> pd.Series:
        return self.pnl.sum(axis=1)

    @property
    def end(self) -> float:
        return float(self.total.iloc[-1]) if len(self.total) else 0.0

    @property
    def worst(self) -> float:
        """Lowest cumulative P&L along the path, counting the start (so never above zero)."""
        return min(0.0, float(self.total.min())) if len(self.total) else 0.0

    @property
    def worst_date(self) -> pd.Timestamp | None:
        if not len(self.total) or self.worst == 0.0:
            return None
        return pd.Timestamp(self.total.idxmin())

    def contributors(self, n: int = 3) -> list[tuple[str, float]]:
        """Largest per-instrument P&L at the worst session (at the end when nothing lost)."""
        if not len(self.total):
            return []
        at = self.worst_date if self.worst_date is not None else self.total.index[-1]
        row = self.pnl.loc[at]
        order = row.abs().sort_values(ascending=False).index[:n]
        return [(str(r), float(row[r])) for r in order if row[r] != 0]


def shock_book(
    book: Mapping[str, float],
    notional: Mapping[str, float],
    ev: Event,
    closes: pd.DataFrame,
    scope: str = "named",
    severity: float = 1.0,
    sectors: Mapping[str, str] | None = None,
) -> Shock:
    held = [r for r, q in book.items() if q != 0]
    moves, uncovered = event_moves(ev, closes, in_scope(held, ev, scope, sectors), severity)
    missing = [r for r in moves if r not in notional]
    if missing:
        raise ValueError(f"no contract size or last price for {missing}")
    pnl = pd.DataFrame({r: book[r] * notional[r] * m.levels for r, m in moves.items()})
    if pnl.empty:
        cal = next(iter(moves.values())).levels.index if moves else pd.DatetimeIndex([])
        pnl = pd.DataFrame(index=cal)
    return Shock(pnl=pnl, uncovered=tuple(uncovered), moves=moves)


def parse_edits(spec: str) -> dict[str, float]:
    """`"6S=0, ZT=-1"` -> {"6S": 0.0, "ZT": -1.0}: target contracts per root, absolute."""
    edits: dict[str, float] = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        sym, sep, qty = part.partition("=")
        if not sep or not sym.strip():
            raise ValueError(f"edit {part!r} is not SYMBOL=CONTRACTS")
        edits[sym.strip().upper()] = float(qty)
    return edits


def apply_edits(book: Mapping[str, float], edits: Mapping[str, float]) -> dict[str, float]:
    out = dict(book)
    out.update(edits)
    return {r: q for r, q in out.items() if q != 0}
