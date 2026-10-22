"""Shared maths for Polymarket intra-venue (NegRisk / complement) arbitrage research.

RESEARCH ONLY: nothing here places orders.

Mechanics (docs.polymarket.com, verified 2026-10-12):

* Fees: taker pays ``fee = C * rate * (p*(1-p))**exponent`` (exponent = 1 on every market seen), makers
  pay 0. ``rate`` comes from the market's ``feeSchedule`` (gamma); ``feesEnabled=false`` -> 0. The fee is
  symmetric in p, so buying NO at 1-b costs the same fee per share as selling YES at b.
* A binary market has ONE book: the NO book is the exact mirror of the YES book (ask_NO = 1 - bid_YES).
  So best_ask(YES) + best_ask(NO) = 1 + spread >= 1 by construction; the matching engine itself crosses
  a YES buy with a NO buy by minting. Type (1) "YES+NO < 1" cannot exist on the book.
* NegRisk event with outcomes i = 1..n (exactly one YES, or "Other"):
    - BUY side: buy 1 YES of every outcome for sum(ask_i) + fees < 1 -> collect $1 at resolution.
      Capital is locked until resolution (there is no instant merge of a cross-market YES set).
      Valid only if EVERY unresolved outcome is tradable (augmented events with unnamed placeholders fail).
    - SELL side: if sum over a subset S (|S| = k >= 2) of bid_i > 1: buy NO_i for i in S at ask_NO = 1 - bid_i
      (i.e. hit the YES bids), then CONVERT the k NOs (NegRisk adapter / V2 Router, atomic) into k-1 USDC
      plus 1 YES of every outcome outside S (worth >= 0). Profit per set = sum_S bid_i - 1 - fees.
      Capital per set = sum_S (1 - bid_i) + fees ~= k - 1 - profit, returned at conversion (seconds,
      gasless via the relayer). Works for augmented events too (placeholders become free YES).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

Level = Tuple[float, float]  # (price, size in shares)


def fee_per_share(p: float, rate: float, exponent: float = 1.0) -> float:
    """Polymarket taker fee per share (USDC) at price p."""
    if rate <= 0 or p <= 0 or p >= 1:
        return 0.0
    return rate * (p * (1.0 - p)) ** exponent


@dataclass
class SetFill:
    """Result of walking several ladders for equal share quantities on every leg."""

    tranches: List[Tuple[float, float]] = field(default_factory=list)  # (shares, net edge per set)
    shares: float = 0.0  # total sets with positive net edge
    profit: float = 0.0  # USDC
    capital: float = 0.0  # USDC needed to put on `shares` sets
    top_edge: float = 0.0  # net edge per set at the top of book (can be negative)

    def at_capital(self, cap: float) -> Tuple[float, float]:
        """(shares, profit) achievable with at most `cap` USDC of capital (tranches taken in order)."""
        used = prof = sh = 0.0
        for q, edge, cps in self._tr_cap:
            if edge <= 0:
                break
            room = cap - used
            if room <= 0:
                break
            take = min(q, room / cps) if cps > 0 else q
            used += take * cps
            prof += take * edge
            sh += take
        return sh, prof

    _tr_cap: List[Tuple[float, float, float]] = field(default_factory=list)  # (q, edge, capital per set)


def _walk(ladders: Sequence[Sequence[Level]], unit_value: float, side: str,
          rates: Sequence[float], exponents: Optional[Sequence[float]] = None,
          min_shares: float = 0.0) -> SetFill:
    """Walk ladders taking equal shares on every leg.

    side='buy'  : ladders are YES asks (ascending). per-set edge = 1 - sum(ask + fee).
                  capital per set = sum(ask + fee).
    side='sell' : ladders are YES bids (descending). per-set edge = sum(bid - fee) - 1.
                  capital per set = sum(1 - bid + fee) (buying NOs).
    """
    n = len(ladders)
    out = SetFill()
    if n == 0 or any(len(l) == 0 for l in ladders):
        out.top_edge = float("nan")
        return out
    exps = exponents or [1.0] * n
    idx = [0] * n
    rem = [ladders[i][0][1] for i in range(n)]
    first = True
    while True:
        px = [ladders[i][idx[i]][0] for i in range(n)]
        fees = [fee_per_share(px[i], rates[i], exps[i]) for i in range(n)]
        if side == "buy":
            edge = unit_value - math.fsum(px) - math.fsum(fees)
            cps = sum(px) + sum(fees)
        else:
            edge = math.fsum(px) - math.fsum(fees) - unit_value
            cps = sum(1.0 - p for p in px) + sum(fees)
        if first:
            out.top_edge = edge
            first = False
        q = min(rem)
        out.tranches.append((q, edge))
        out._tr_cap.append((q, edge, cps))
        if edge <= 0:
            break
        out.shares += q
        out.profit += q * edge
        out.capital += q * cps
        # advance legs whose level is exhausted
        done = False
        for i in range(n):
            rem[i] -= q
            if rem[i] <= 1e-9:
                idx[i] += 1
                if idx[i] >= len(ladders[i]):
                    done = True
                else:
                    rem[i] = ladders[i][idx[i]][1]
        if done:
            break
    if out.shares < min_shares:  # venue minimum order size (5 shares) on every leg
        out.shares = out.profit = out.capital = 0.0
        out._tr_cap = []
    return out


def buy_all_yes(asks: Sequence[Sequence[Level]], rates: Sequence[float], **kw) -> SetFill:
    """Buy one YES of every outcome (asks sorted ascending)."""
    return _walk([sorted(a) for a in asks], 1.0, "buy", rates, **kw)


def sell_all_yes(bids: Sequence[Sequence[Level]], rates: Sequence[float], **kw) -> SetFill:
    """Hit YES bids on every leg of subset S (= buy NOs and convert). Bids sorted descending."""
    return _walk([sorted(b, key=lambda x: -x[0]) for b in bids], 1.0, "sell", rates, **kw)


def best_sell_subset(bids: Sequence[Sequence[Level]], rates: Sequence[float]) -> Tuple[List[int], float]:
    """Choose the subset S maximising top-of-book net edge per set: include every leg whose top bid net of
    fee is > 0 (each such leg adds to sum(bid - fee)); need |S| >= 2. Returns (indices, top edge)."""
    tops = []
    for i, b in enumerate(bids):
        if b:
            p = max(x[0] for x in b)
            tops.append((p - fee_per_share(p, rates[i]), i))
    tops.sort(reverse=True)
    S = [i for v, i in tops if v > 0]
    if len(S) < 2:
        S = [i for _, i in tops[:2]]
    edge = sum(v for v, i in tops if i in S) - 1.0
    return S, edge


def parse_levels(levels) -> List[Level]:
    out = []
    for l in levels or []:
        try:
            p, s = float(l["price"]), float(l["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if s > 0 and 0 < p < 1:
            out.append((p, s))
    return out


def topbook_edges(A, B, AS, BS, rates):
    """Vectorised top-of-book edges. A/B/AS/BS: (T, n) arrays of best ask/bid price/size per leg (NaN = no
    quote); rates: (n,). Returns (buy_edge, sell_edge, buy_size, sell_size, sell_k) each (T,).
    buy  = 1 - sum(ask + fee)  (NaN if any leg has no ask)
    sell = sum over legs with bid-fee > 0 of (bid - fee) - 1 (at least the best two legs)."""
    import numpy as np

    rates = np.asarray(rates, dtype=float)
    fa = rates * A * (1 - A)
    fb = rates * B * (1 - B)
    buy = 1 - np.sum(A + fa, axis=1)  # NaN propagates if any leg lacks an ask
    nb = np.where(np.isnan(B), -1.0, B - fb)
    npos = (nb > 0).sum(axis=1)
    pos = np.where(nb > 0, nb, 0).sum(axis=1)
    two = -np.sort(-nb, axis=1)[:, :2].sum(axis=1)
    sell = np.where(npos >= 2, pos, two) - 1
    buy_sz = np.min(np.where(np.isnan(AS), 0, AS), axis=1)
    sel_mask = np.where(npos[:, None] >= 2, nb > 0, nb >= -np.sort(-nb, axis=1)[:, [1]])
    sell_sz = np.where(sel_mask, np.nan_to_num(BS, nan=0), np.inf).min(axis=1)
    return buy, sell, buy_sz, sell_sz, np.maximum(npos, 2)


def episodes(t, edge, size=None):
    """Maximal runs of edge > 0 on an event-time grid t (any unit). Returns list of (i_start, i_end_excl)."""
    import numpy as np

    on = np.nan_to_num(edge, nan=-1) > 0
    d = np.diff(np.concatenate([[0], on.astype(int), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))
