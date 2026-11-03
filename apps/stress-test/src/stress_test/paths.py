"""An event as daily bars the engine can trade through: today's prices moved by the event.

The moves are Tier 1's (scenario.event_moves: the event's real path per instrument, rescaled to
today's volatility, floored), so the static and the dynamic replay move the same instruments by the
same amounts, applied to each instrument's last close. Session i of the event becomes the i-th
weekday after the last bar.

A replay moves every instrument the scope names, held or not (the strategy may enter one). Every
other instrument keeps today's close as its level but not a flat price: it alternates half of its
current daily standard deviation above and below that close. A flat price would make the engine's
volatility estimate decay and its position in the instrument grow session by session (an artifact,
not a reaction); the alternation keeps the estimate where it is, carries no trend, and its P&L on a
held contract stays within half a day's standard deviation. Each bar also gets the instrument's own
recent volume and intraday range: the engine's session classifier treats a bar with no range
(high == low) or a fraction of the usual volume as junk and would hold the symbol instead of
trading it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .catalogue import Event
from .scenario import Move, calendar, daily_vol, event_moves
from .shock import in_scope
from .snapshot import Snapshot, root

RECENT_SESSIONS = 20  # volume and range norms: the last month of weekday sessions


@dataclass(frozen=True)
class EventPath:
    bars: pd.DataFrame  # date, symbol, open, high, low, close, volume, instrument_id
    dates: pd.DatetimeIndex  # the replay's sessions
    calendar: pd.DatetimeIndex  # the historical sessions they stand for
    moves: dict[str, Move]  # root -> the move applied
    uncovered: tuple[str, ...]  # roots the scope names that our history cannot cover
    levels: pd.DataFrame  # index: the replay's sessions; columns: root; close / base - 1
    base: dict[str, float]  # root -> the last close the path starts from


def history_for_replay(bars: pd.DataFrame, snap: Snapshot) -> pd.DataFrame:
    """Every stored bar before the day, weekend prints included: the history production's run for
    the day consumed, so the strategy starts the event in the state that produced the stored book
    (an instrument that trades at the weekend, MBT, moved after Friday's close)."""
    return bars[bars["date"] < pd.Timestamp(snap.day)].copy()


def roll_adjusted(bars: pd.DataFrame) -> pd.DataFrame:
    """The bars with every roll's price gap taken out, for an engine that cannot read a bar's
    contract id (the published module's Bar has none).

    The stored series are the vendor's raw front month: on the bar whose contract id changes, the
    move from the previous close is mostly the gap between two contracts. Production reads the ids
    and books no P&L on that bar; an engine without them would read the gap as a move, in its
    volatility estimate (a monthly-rolling contract such as CL looks far riskier than it is and is
    sized to nothing) and in its P&L. Here the return of each id-change bar is set to 0 and every
    earlier bar is scaled to fit, so the series ends at today's real prices and moves as the held
    contract did. A bar with no id never counts as a change.
    """
    out = []
    for _, g in bars.sort_values(["symbol", "date"]).groupby("symbol", sort=False):
        g = g.copy()
        ids = g["instrument_id"].astype(str).replace({"nan": ""})
        prev = ids.shift(1).fillna("")
        change = (ids != prev) & (ids != "") & (prev != "")
        ratio = g["close"] / g["close"].shift(1)
        # factor to apply to every bar BEFORE a change bar: the change bar's own ratio
        step = ratio.where(change, 1.0).fillna(1.0)
        after = step[::-1].cumprod()[::-1].shift(-1).fillna(1.0)  # product of later changes
        for c in ("open", "high", "low", "close"):
            g[c] = g[c] * after
        out.append(g)
    return pd.concat(out).sort_values(["symbol", "date"]).reset_index(drop=True)


def replay_dates(after: pd.Timestamp, n: int) -> pd.DatetimeIndex:
    return pd.bdate_range(after + pd.offsets.BDay(1), periods=n)


def _norms(history: pd.DataFrame) -> pd.DataFrame:
    """Per symbol: last close and id (any day), median recent weekday volume and half the median
    weekday intraday range (weekend prints are thin and would understate both)."""
    last = history.sort_values(["symbol", "date"]).groupby("symbol").tail(1).set_index("symbol")
    wk = history[history["date"].dt.dayofweek < 5].sort_values(["symbol", "date"])
    recent = wk.groupby("symbol").tail(RECENT_SESSIONS)
    rng = ((recent["high"] - recent["low"]) / recent["close"]).groupby(recent["symbol"]).median()
    vol = recent.groupby("symbol")["volume"].median()
    return pd.DataFrame(
        {
            "close": last["close"],
            "instrument_id": last["instrument_id"],
            "volume": vol,
            "half_range": (rng / 2).clip(lower=1e-4).fillna(1e-3),
        }
    )


def _still(snap: Snapshot, r: str, n: int) -> np.ndarray:
    """An unmoved instrument's levels: +half, -half, ... of today's daily standard deviation."""
    sd = daily_vol(snap.closes[r]) if r in snap.closes.columns else float("nan")
    half = 0.5 * (sd if np.isfinite(sd) and sd > 0 else 0.01)
    return half * np.where(np.arange(n) % 2 == 0, 1.0, -1.0)


def event_path(
    snap: Snapshot,
    history: pd.DataFrame,
    ev: Event | None,
    scope: str = "sector",
    severity: float = 1.0,
    sessions: int | None = None,
) -> EventPath:
    """`ev=None` is the no-event path of `sessions` sessions: no instrument moves."""
    norms = _norms(history)
    roots = {root(s): s for s in norms.index}
    if ev is not None:
        cal = calendar(ev, snap.closes)
        named = in_scope(sorted(roots), ev, scope, snap.sectors)
        moves, uncovered = event_moves(ev, snap.closes, named, severity)
    else:
        cal = pd.DatetimeIndex([])
        moves, uncovered = {}, []
    n = len(cal) if ev is not None else int(sessions or 0)
    if n == 0:
        raise ValueError("an event path needs at least one session")
    dates = replay_dates(history["date"].max(), n)
    frames = []
    levels_by_root: dict[str, np.ndarray] = {}
    base: dict[str, float] = {}
    for r, sym in sorted(roots.items()):
        nm = norms.loc[sym]
        p0 = float(nm["close"])  # the last close the strategy consumed before the event
        levels = moves[r].levels.to_numpy(dtype=float) if r in moves else _still(snap, r, n)
        close = p0 * (1 + levels)
        levels_by_root[r], base[r] = levels, p0
        open_ = np.concatenate([[p0], close[:-1]])
        hr = float(nm["half_range"])
        frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": sym,
                    "open": open_,
                    "high": np.maximum(open_, close) * (1 + hr),
                    "low": np.minimum(open_, close) * (1 - hr),
                    "close": close,
                    "volume": float(nm["volume"]) if pd.notna(nm["volume"]) else 1000.0,
                    "instrument_id": nm["instrument_id"],
                }
            )
        )
    return EventPath(
        bars=pd.concat(frames, ignore_index=True),
        dates=dates,
        calendar=cal,
        moves=moves,
        uncovered=tuple(uncovered),
        levels=pd.DataFrame(levels_by_root, index=dates),
        base=base,
    )
