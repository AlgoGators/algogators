"""Kalshi Liquidity Incentive Program (LIP) scoring, as documented on help.kalshi.com
("Liquidity Incentive Program", fetched 2026-10-14; programme end 2027-01-01).

Per snapshot (once a second), per side (YES bids and NO bids are scored separately):
  1. If total resting size on EITHER side < Target Size, the snapshot is excluded (nobody earns).
  2. Reference price: walking down from the best bid, the first level where cumulative size >= Target/5.
  3. Qualifying orders: those that help reach Target Size, walking down in priority order.
  4. Multiplier: 1.0 at or better than the reference price, else DF ** ticks_below_reference.
  5. Side share = own score / all qualifying score on that side. Snapshot score = yes share + no share (<= 2).
Reward = pool * sum_over_snapshots(own snapshot score) / (2 * total snapshots)   [docs example: $16 = 100*.2*.8].

Two conventions for the level that crosses Target:
  'level'   - the whole crossing price level counts. THIS IS THE OFFICIAL RULE: the CFTC filing of 2026-07-15
              (Appendix A, data/vendor/klip/docs/cftc_lip_2026-07-15.txt) adds "all bids at the current bid
              price" and stops once the cumulative size >= Target. No time priority inside a level.
  'partial' - only the part of the crossing level needed to reach Target counts (sensitivity only).
"""
from __future__ import annotations

import math

import numpy as np

TICK = 0.01


def side_score(prices, sizes, target, df, our_px=None, our_sz=0.0, mode="level"):
    """prices/sizes: one side's resting bids (any order). Returns (qualifies, our_score, total_score).
    total_score includes ours. Our order sits behind existing size at our_px."""
    lv = [(float(p), float(s), 0) for p, s in zip(prices, sizes) if s > 0]
    if our_px is not None and our_sz > 0:
        lv.append((float(our_px), float(our_sz), 1))
    # price desc, existing (0) before ours (1) at the same price
    lv.sort(key=lambda x: (-x[0], x[2]))
    tot = sum(s for _, s, _ in lv)
    if tot + 1e-9 < target:
        return False, 0.0, 0.0
    # reference price
    cum = 0.0; ref = lv[-1][0]
    for p, s, _ in lv:
        cum += s
        if cum + 1e-9 >= target / 5.0:
            ref = p; break
    cum = 0.0; ours = 0.0; total = 0.0; last_p = None
    for p, s, me in lv:
        if cum + 1e-9 >= target and not (mode == "level" and last_p is not None and abs(p - last_p) < 1e-9):
            break
        last_p = p
        take = s if mode == "level" else min(s, target - cum)
        ticks = max(0, int(round((ref - p) / TICK)))
        mult = 1.0 if p >= ref - 1e-9 else df ** ticks
        sc = take * mult
        total += sc
        if me:
            ours += sc
        cum += s
    return True, ours, total


def snapshot_share(yes, no, target, df, our_yes=None, our_no=None, mode="level"):
    """yes/no: (prices, sizes) of YES bids and NO bids. our_*: (px, sz) or None.
    Returns our snapshot score in [0, 2] (0 if excluded) and the excluded flag."""
    qy, oy, ty = side_score(*yes, target, df, *(our_yes or (None, 0.0)), mode=mode)
    qn, on, tn = side_score(*no, target, df, *(our_no or (None, 0.0)), mode=mode)
    if not (qy and qn):
        return 0.0, True
    s = (oy / ty if ty > 0 else 0.0) + (on / tn if tn > 0 else 0.0)
    return s, False


def payout(pool_usd, own_scores, n_total):
    """Docs formula. own_scores: our snapshot scores over NON-excluded snapshots."""
    n_ok = len(own_scores)
    if n_ok == 0:
        return 0.0
    period_score = float(np.sum(own_scores)) / (2.0 * n_ok)
    return math.floor(100 * period_score * pool_usd * n_ok / n_total) / 100
