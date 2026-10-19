"""Causal passive-fill engine (Fa / Fb / Fc), fused: all requested models in ONE pass over the tape.

Promoted from scripts/research/mkgap_causal._engine (itself a numba port of audit_flb_common.causal_opps); the
semantics are unchanged and tests/unit/test_fills_engine.py checks fill-for-fill equality against both.

Inputs are integer cents and must be sorted by (ticker, time) with tickers contiguous:
  tk    int   ticker code per print (contiguous runs)
  sec   int   print second (the engine groups prints by second; state updates only between seconds)
  yc    int   YES price in cents
  tyes  bool  taker bought YES
  qty   float contracts
  elig  bool  print's second is eligible to fill (window / split filter evaluated at the first print of the second)
  q0    float per-print visible queue ahead of us (Fc only; read at the first print of the second we re-peg)
Models (see audit_flb_common docstring):
  Fa  q = last-hit + 1c (cap 99; join on a 1c spread); fills on through-prints and at-level hits when alone; share .50
  Fb  q = last-hit; through-prints only; share .25
  Fc  q = last-hit behind q0; at-level hits drain the queue first; share .10; back of the queue after a fill/re-peg
Output per fill: row (first print of the second), model, side (0 YES bid, 1 NO bid), q (cents), fq = floor(fill qty),
through and at-level contracts in that second. Order: ticker, side, second, model.
"""
from __future__ import annotations

from dataclasses import dataclass

import numba as nb
import numpy as np

MODELS = {"Fa": (0, 0.50), "Fb": (1, 0.25), "Fc": (2, 0.10)}
MODEL_NAMES = np.array(["Fa", "Fb", "Fc"], dtype=object)


@nb.njit(cache=True)
def _n_groups(tk, sec):
    """Number of (ticker, second) groups = upper bound on fills per side per model."""
    n = len(tk)
    g = 0
    for i in range(n):
        if i == 0 or tk[i] != tk[i - 1] or sec[i] != sec[i - 1]:
            g += 1
    return g


@nb.njit(cache=True)
def _fused(tk, sec, yc, tyes, qty, elig, q0arr, use, share):
    n = len(tk)
    nm = 0
    for j in range(3):
        if use[j]:
            nm += 1
    cap = 2 * _n_groups(tk, sec) * nm + 2  # tight bound (not 2*n*nm): output arrays are committed memory
    o_idx = np.empty(cap, np.int64); o_m = np.empty(cap, np.int8); o_side = np.empty(cap, np.int8)
    o_q = np.empty(cap, np.int16); o_fq = np.empty(cap, np.int64)
    o_th = np.empty(cap, np.float64); o_at = np.empty(cap, np.float64)
    no = 0
    qm = np.empty(3, np.int64); queue = np.zeros(3, np.float64)
    a = 0
    while a < n:
        b = a
        while b < n and tk[b] == tk[a]:
            b += 1
        for side in range(2):  # 0 = YES bid, 1 = NO bid
            last_hit = -1; last_ask = -1
            for j in range(3):
                qm[j] = -1; queue[j] = 0.0
            k = a
            while k < b:
                s = sec[k]; m = k
                while m < b and sec[m] == s:
                    m += 1
                if last_hit >= 0:
                    tga = min(last_hit + 1, 99)
                    if last_ask >= 0 and tga >= last_ask:
                        tga = last_hit
                    for j in range(3):
                        if use[j]:
                            tgt = tga if j == 0 else last_hit
                            if qm[j] < 0 or qm[j] != tgt:
                                qm[j] = tgt
                                queue[j] = q0arr[k] if j == 2 else 0.0
                if elig[k]:
                    # through / at-level volume for the (at most two) distinct levels in play this second
                    qa = qm[0]; qb = qm[1] if use[1] else qm[2]
                    th_a = 0.0; at_a = 0.0; th_b = 0.0; at_b = 0.0
                    if (use[0] and qa >= 1 and qa <= 99) or ((use[1] or use[2]) and qb >= 1 and qb <= 99):
                        for r in range(k, m):
                            p = yc[r] if side == 0 else 100 - yc[r]
                            hit = (not tyes[r]) if side == 0 else tyes[r]
                            if p <= qa - 1:
                                th_a += qty[r]
                            elif hit and p == qa:
                                at_a += qty[r]
                            if p <= qb - 1:
                                th_b += qty[r]
                            elif hit and p == qb:
                                at_b += qty[r]
                    for j in range(3):
                        if not use[j]:
                            continue
                        q = qm[j]
                        if q < 1 or q > 99:
                            continue
                        if j == 0:
                            through = th_a; atl = at_a
                            alone = q > last_hit
                            fq = share[0] * (through + (atl if alone else 0.0))
                        elif j == 1:
                            through = th_b; atl = at_b
                            fq = share[1] * through
                        else:
                            through = th_b; atl = at_b
                            u = min(queue[2], atl); queue[2] -= u
                            fq = share[2] * (through + atl - u)
                        if fq >= 1:
                            o_idx[no] = k; o_m[no] = j; o_side[no] = side; o_q[no] = q
                            o_fq[no] = int(np.floor(fq)); o_th[no] = through; o_at[no] = atl; no += 1
                            if j == 2:
                                queue[2] = q0arr[k]
                for r in range(k, m):
                    p = yc[r] if side == 0 else 100 - yc[r]
                    hit = (not tyes[r]) if side == 0 else tyes[r]
                    if hit:
                        last_hit = p
                    else:
                        last_ask = p
                k = m
        a = b
    # copy so the (larger) work buffers are freed
    return (o_idx[:no].copy(), o_m[:no].copy(), o_side[:no].copy(), o_q[:no].copy(), o_fq[:no].copy(),
            o_th[:no].copy(), o_at[:no].copy())


@dataclass
class Fills:
    """Struct of arrays; `row` indexes the input arrays (first print of the fill second)."""
    row: np.ndarray
    model: np.ndarray   # int8: 0 Fa, 1 Fb, 2 Fc
    side: np.ndarray    # int8: 0 YES bid, 1 NO bid
    q: np.ndarray       # int16 cents
    fq: np.ndarray      # int64 contracts (floored, share-scaled)
    through: np.ndarray
    atl: np.ndarray

    def __len__(self):
        return len(self.row)

    def select(self, mask) -> "Fills":
        return Fills(*(getattr(self, f)[mask] for f in ("row", "model", "side", "q", "fq", "through", "atl")))

    def by_model(self, name: str) -> "Fills":
        return self.select(self.model == MODELS[name][0])


def causal_fills(tk, sec, yc, tyes, qty, elig=None, q0=None, models=("Fa", "Fb", "Fc"), shares=None) -> Fills:
    """Run the requested fill models in one pass. shares: optional {model: share} override (defaults .5/.25/.1)."""
    n = len(tk)
    use = np.zeros(3, np.bool_); share = np.array([MODELS[m][1] for m in ("Fa", "Fb", "Fc")], np.float64)
    for m in models:
        use[MODELS[m][0]] = True
    for m, s in (shares or {}).items():
        share[MODELS[m][0]] = s
    elig = np.ones(n, np.bool_) if elig is None else np.ascontiguousarray(elig, np.bool_)
    q0 = np.zeros(n, np.float64) if q0 is None else np.ascontiguousarray(np.broadcast_to(q0, (n,)), np.float64)
    out = _fused(np.ascontiguousarray(tk, np.int64), np.ascontiguousarray(sec, np.int64),
                 np.ascontiguousarray(yc, np.int64), np.ascontiguousarray(tyes, np.bool_),
                 np.ascontiguousarray(qty, np.float64), elig, q0, use, share)
    return Fills(*out)
