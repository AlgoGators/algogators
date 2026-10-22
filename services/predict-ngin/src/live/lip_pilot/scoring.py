"""LIP scoring per the Kalshi help text and Appendix A of the 2026-07-15 CFTC filing.

Per one-second snapshot, per side (YES bids and NO bids separately):
- if either side's total resting size < Target, the snapshot is excluded (nobody earns);
- reference price = walking down from the best bid, the first level where cumulative size >= Target/5;
- qualifying levels are walked down until cumulative size >= Target (the whole crossing level counts);
- each order scores size x DF^(ticks below the reference) (1.0 at or above the reference);
- side share = our score / total qualifying score.
Payout = pool x sum(our snapshot scores) / (2 x all snapshots); the share of NON-excluded snapshots enters
through the sum. Minimum payout $1, rounded down to the cent.

Same convention as scripts/research/klip_lip.py ('level') and the independent lipgas_reward.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

Level = Tuple[float, float]  # (price in dollars, size in contracts)


@dataclass(frozen=True)
class SideScore:
    qualifies: bool       # side reaches Target (incl. our order)
    reference: Optional[float]
    ours: float
    total: float

    @property
    def share(self) -> float:
        return self.ours / self.total if self.qualifies and self.total > 0 else 0.0


def reference_price(levels: Sequence[Level], target: float) -> Optional[float]:
    cum = 0.0
    for p, s in sorted(levels, key=lambda x: -x[0]):
        cum += s
        if cum + 1e-9 >= target / 5.0:
            return p
    return None


def side_score(levels: Iterable[Level], target: float, df: float, tick: float = 0.01,
               our_px: Optional[float] = None, our_sz: float = 0.0) -> SideScore:
    """``levels``: OTHERS' resting bids on one side. Our order sits behind existing size at our price."""
    lv: List[Tuple[float, float, int]] = [(float(p), float(s), 0) for p, s in levels if s > 0]
    if our_px is not None and our_sz > 0:
        lv.append((float(our_px), float(our_sz), 1))
    lv.sort(key=lambda x: (-x[0], x[2]))
    tot = sum(s for _, s, _ in lv)
    if not lv or tot + 1e-9 < target:
        return SideScore(False, None, 0.0, 0.0)
    ref = reference_price([(p, s) for p, s, _ in lv], target)
    cum = ours = total = 0.0
    last_p = None
    for p, s, me in lv:
        if cum + 1e-9 >= target and not (last_p is not None and abs(p - last_p) < 1e-9):
            break
        last_p = p
        ticks = max(0, int(round((ref - p) / tick))) if ref is not None else 0
        mult = 1.0 if ref is None or p >= ref - 1e-9 else df ** ticks
        total += s * mult
        if me:
            ours += s * mult
        cum += s
    return SideScore(True, ref, ours, total)


def snapshot_score(yes_others: Sequence[Level], no_others: Sequence[Level], target: float, df: float,
                   our_yes: Optional[Tuple[float, float]] = None, our_no: Optional[Tuple[float, float]] = None,
                   tick: float = 0.01) -> Tuple[float, bool, SideScore, SideScore]:
    """Returns (our snapshot score in [0, 2], excluded flag, yes SideScore, no SideScore)."""
    sy = side_score(yes_others, target, df, tick, *(our_yes or (None, 0.0)))
    sn = side_score(no_others, target, df, tick, *(our_no or (None, 0.0)))
    if not (sy.qualifies and sn.qualifies):
        return 0.0, True, sy, sn
    return sy.share + sn.share, False, sy, sn


def reward_rate_usd_per_s(period_reward_usd: float, period_seconds: float, snapshot: float) -> float:
    """Expected reward accrual per second for one snapshot score (pool / period / 2 x score)."""
    if period_seconds <= 0:
        return 0.0
    return period_reward_usd / period_seconds / 2.0 * snapshot


def payout(pool_usd: float, own_scores: Sequence[float], n_total: int) -> float:
    """Help-page formula: own_scores over NON-excluded snapshots; $1 minimum, floor to the cent."""
    n_ok = len(own_scores)
    if n_ok == 0 or n_total <= 0:
        return 0.0
    period_score = float(sum(own_scores)) / (2.0 * n_ok)
    amt = math.floor(100 * period_score * pool_usd * n_ok / n_total) / 100
    return amt if amt >= 1.0 else 0.0
