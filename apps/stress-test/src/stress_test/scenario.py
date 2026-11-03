"""An event's per-instrument moves, recomputed from our prices and rescaled to today's volatility.

Conventions follow the package's generator (engine/synth_ab.py) so Tier 1 and a later
dynamic replay describe the same event:

- Single-session events: the move from the previous close to the event day's close.
- Window events: the path of closes relative to the close on the window's first session
  (that session is treated as ordinary, as the catalogue measured it).
- Volatility translation: k = clip(today's 252-session vol / the 252 sessions before the
  event, 0.5, 2.0), applied in log space, (1 + level) ** (k * severity) - 1, so a crash
  approaches zero without crossing it. Our continuous series never print a negative price,
  so the generator's linear branch for energy that did cross zero never applies here.
- Realism floor: no price below 80% of the instrument's all-time low in our history.
- Severity 1.0 is the central case. SEVERE is the generator's 95th-percentile draw
  (lognormal sd 0.25), for a "worse than the real one" reading.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .catalogue import Event

VOL_LOOKBACK = 252
VOL_SCALE_CLIP = (0.5, 2.0)
PRICE_FLOOR_OF_HIST_LOW = 0.80
SEVERITY_SD = 0.25
SEVERE = float(np.exp(1.645 * SEVERITY_SD))
# A window instrument needs a close this close to the window start to have a reference.
MAX_REFERENCE_GAP = pd.Timedelta(7, unit="D")


def daily_vol(closes: pd.Series) -> float:
    r = closes.dropna().pct_change().dropna().iloc[-VOL_LOOKBACK:]
    return float(r.std()) if len(r) > 1 else float("nan")


def calendar(ev: Event, closes: pd.DataFrame) -> pd.DatetimeIndex:
    """The sessions of the event: dates in the window where any named instrument printed."""
    if ev.single:
        return pd.DatetimeIndex([ev.start])
    named = [r for r in ev.instruments if r in closes.columns]
    window = closes.loc[ev.start : ev.end, named].dropna(how="all")
    return pd.DatetimeIndex(window.index)


def real_levels(series: pd.Series, ev: Event, cal: pd.DatetimeIndex) -> pd.Series | None:
    """The real move as levels (price / reference - 1) on the event's sessions, or None
    when our history does not cover this instrument at the event."""
    s = series.dropna()
    if ev.single:
        if ev.start not in s.index:
            return None
        i = s.index.get_loc(ev.start)
        if (
            not isinstance(i, int | np.integer)
            or i == 0
            or ev.start - s.index[i - 1] > MAX_REFERENCE_GAP
        ):
            return None
        return pd.Series([s.iloc[i] / s.iloc[i - 1] - 1], index=cal)
    if len(cal) == 0 or s.loc[ev.start : ev.end].empty:
        return None
    before = s.loc[: cal[0]]
    if before.empty or cal[0] - before.index[-1] > MAX_REFERENCE_GAP:
        return None
    path = s.reindex(s.index.union(cal)).ffill().loc[cal]
    return path / before.iloc[-1] - 1


@dataclass(frozen=True)
class Move:
    root: str
    k: float  # volatility rescale, today vs before the event
    real: pd.Series  # the historical levels
    levels: pd.Series  # rescaled and floored levels applied to the book
    floored: bool  # True when the realism floor bound somewhere on the path


def translate(
    real: pd.Series, history: pd.Series, ev: Event, severity: float = 1.0
) -> tuple[float, pd.Series, bool]:
    s = history.dropna()
    hist_vol = daily_vol(s[s.index < ev.start])
    cur_vol = daily_vol(s)
    k = float(np.clip(cur_vol / hist_vol, *VOL_SCALE_CLIP)) if hist_vol > 0 else 1.0
    scaled = np.power(np.maximum(1 + real, 1e-6), k * severity) - 1
    floor = PRICE_FLOOR_OF_HIST_LOW * float(s.min()) / float(s.iloc[-1]) - 1
    floored = bool((scaled < floor).any())
    return k, scaled.clip(lower=floor), floored


def event_moves(
    ev: Event, closes: pd.DataFrame, roots: Iterable[str], severity: float = 1.0
) -> tuple[dict[str, Move], list[str]]:
    """Moves for `roots` under `ev`, plus the roots our history cannot cover at the event.

    `closes` must end at the last bar before the snapshot day: it is both the event's
    history and "today" for the volatility rescale.
    """
    cal = calendar(ev, closes)
    moves: dict[str, Move] = {}
    uncovered: list[str] = []
    for r in roots:
        if r not in closes.columns:
            uncovered.append(r)
            continue
        real = real_levels(closes[r], ev, cal)
        if real is None:
            uncovered.append(r)
            continue
        k, levels, floored = translate(real, closes[r], ev, severity)
        moves[r] = Move(root=r, k=k, real=real, levels=levels, floored=floored)
    return moves, uncovered
