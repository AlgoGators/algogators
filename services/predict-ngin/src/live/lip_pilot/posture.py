"""BB1 / Target-4 posture: the price and size we want resting on one side of one market.

Rule (lipscan F1, docs/LIPSCAN_PREREG.md section 2; lipgas audit):
- price = best bid of OTHERS on that side - ticks_behind (1 tick), within [3c, 97c], never crossing;
- size  = ceil(Target x 1/4) minus what that side has already filled (inventory cap 1x size), further
  cut so this market's max loss <= per-market cap and the book's total <= total cap;
- quote only on a paid snapshot: our side incl. us reaches Target, and the other side's OTHERS reach Target;
- stay at or above the reference price: if BB1 is below the reference (a rival >= Target/5 at the best bid),
  skip the side (default) or join the reference;
- pulled when time to close <= pull minutes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .book import Book
from .config import PostureCfg
from .risk import MarketExposure, max_side_size
from .scoring import reference_price, side_score


@dataclass
class Program:
    program_id: str
    ticker: str
    target: float
    df: float
    period_reward_usd: float
    start_ts: float
    end_ts: float

    @property
    def period_s(self) -> float:
        return max(self.end_ts - self.start_ts, 900.0)

    @property
    def reward_usd_day(self) -> float:
        return self.period_reward_usd / (self.period_s / 86400.0)


@dataclass
class Quote:
    outcome: str
    px: Optional[float]           # None = no order on this side
    size: int
    reason: str
    share: float = 0.0
    reference: Optional[float] = None
    ahead: float = 0.0            # others' size strictly above our price


def _round_tick(p: float, tick: float) -> float:
    return round(round(p / tick) * tick, 4)


def desired_quote(book: Book, outcome: str, prog: Program, cfg: PostureCfg, exposure: MarketExposure,
                  our_px: Optional[float], our_remaining: float,
                  other_px: Optional[float], other_remaining: float,
                  per_market_cap: float, room_total: float, filled_this_side: float) -> Quote:
    other = "no" if outcome == "yes" else "yes"
    others = book.others(outcome, our_px, our_remaining)
    opp_others = book.others(other, other_px, other_remaining)
    if not others:
        return Quote(outcome, None, 0, "no_bid")
    best = others[0][0]
    px = _round_tick(best - cfg.ticks_behind_best_bid * cfg.tick, cfg.tick)
    full = int(math.ceil(prog.target * cfg.size_fraction_of_target - 1e-9))
    want = full - int(round(filled_this_side))
    if want <= 0:
        return Quote(outcome, None, 0, "inventory_cap")

    ahead_bb1 = sum(s for p, s in others if p > px + 1e-9)
    ref = reference_price(others + [(px, float(want))], prog.target)
    if ref is not None and px < ref - 1e-9:
        if cfg.when_below_reference == "join_reference":
            px = _round_tick(ref, cfg.tick)
        else:
            return Quote(outcome, None, 0, "below_reference", reference=ref, ahead=ahead_bb1)
    if not (cfg.price_min - 1e-9 <= px <= cfg.price_max + 1e-9):
        return Quote(outcome, None, 0, "price_band")
    opp_best_all = book.best_bid(other)
    if opp_best_all is not None and px + opp_best_all >= 1.0 - 1e-9:
        return Quote(outcome, None, 0, "would_cross")

    exp = MarketExposure(**vars(exposure))
    if outcome == "yes":
        exp.yes_resting, exp.yes_px = 0.0, 0.0
    else:
        exp.no_resting, exp.no_px = 0.0, 0.0
    cap_n = max_side_size(exp, outcome, px, per_market_cap, room_total, cfg.maker_fee_per_contract)
    size = min(want, cap_n)
    if size < cfg.min_side_size:
        return Quote(outcome, None, 0, "cap_too_small", reference=ref)

    ss = side_score(others, prog.target, prog.df, cfg.tick, px, float(size))
    opp_total = sum(s for _, s in opp_others)
    if cfg.require_paid_snapshot and (not ss.qualifies or opp_total + 1e-9 < prog.target):
        return Quote(outcome, None, 0, "unpaid_snapshot", reference=ss.reference)
    ahead = sum(s for p, s in others if p > px + 1e-9)
    return Quote(outcome, px, int(size), "ok", share=ss.share, reference=ss.reference, ahead=ahead)
