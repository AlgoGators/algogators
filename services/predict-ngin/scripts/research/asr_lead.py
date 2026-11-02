"""ASR lead / paper P&L given that the absolute audio->market anchor is not identified.

Structure found (see doc): each said word reprices in ONE taker-YES sweep of the ask ladder, at a near-constant
delay after the audio. So
  d_i  = J_move_i - (release + s_i)                (contains the unknown anchor error + market lag)
  r_i  = d_i - median_{j != i, same event} d_j     (LOO residual: per-word timing spread, identified)
  lead_i(L, V) = L + r_i - asr_lat_i - V - 1       (L = the event's typical speech->sweep lag, NOT identified)
Paper fills:
  (a) pre-registered proxy: taker-NO prints in (D_i, Jh_i], D_i = J_move_i - lead_i (bought YES at their price)
  (b) post-hoc 'ladder prize': if lead_i > 0, the YES asks that the winning sweep lifted within 2 s of J_move with
      y < 0.95 (qty x (1 - y) - taker fee) -- an upper bound on what a faster detector could have taken.
  False positives (detected, settled NO, p0 < 0.9): we would have lifted asks; cost = the event-family median
  ladder cost (contracts x price + fee) of true sweeps, lost in full.
Outputs: data/vendor/asr/lead_words.csv, lead_grid.csv, spread.csv
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from asr_common import EVENTS, OUT
from asr_eval import MATCH_S, boot_ci, loo_center, run_event, taker_fee

LS = (1.0, 2.0, 3.0, 5.0, 10.0, 20.0)
VS = (0.0, 5.0, 15.0)
SWEEP_S = 2.0
CAP = 0.95


def prize(tape, tk, jm):
    g = tape[(tape.ticker == tk) & (tape.t >= jm) & (tape.t <= jm + SWEEP_S) & tape.tyes & (tape.y < CAP)]
    c = float(g.qty.sum())
    gain = float((g.qty * (1 - g.y)).sum()) - sum(taker_fee(y, q) for y, q in zip(g.y, g.qty))
    cost = float((g.qty * g.y).sum()) + sum(taker_fee(y, q) for y, q in zip(g.y, g.qty))
    return c, gain, cost


def no_proxy(tape, tk, D, end):
    g = tape[(tape.ticker == tk) & (tape.t > D) & (tape.t <= end) & ~tape.tyes]
    return float(g.qty.sum()), float((g.qty * (1 - g.y)).sum()) - sum(taker_fee(y, q) for y, q in zip(g.y, g.qty))


def main():
    sched = json.loads((OUT / "schedule.json").read_text())
    words, fps = [], []
    for ev in EVENTS:
        for prov in ("dg", "aai"):
            r = run_event(ev, prov, sched[ev])
            if r is None:
                continue
            x, lat, tape = r
            for pol in ("int", "fin"):
                y = x[(x.policy == pol)].copy()
                y["d"] = y.J_move - y.spoken_wall
                tp = y[(y.y01 == 1) & y.s.notna() & (y.p0 < 0.90) & y.J_move.notna()].copy()
                if len(tp) >= 3:
                    tp["r"] = [row.d - loo_center(tp.d.drop(i)) for i, row in tp.iterrows()]
                else:
                    tp["r"] = np.nan
                pz = [prize(tape, t, jm) for t, jm in zip(tp.ticker, tp.J_move)]
                tp["prize_ctr"] = [p[0] for p in pz]
                tp["prize_gain"] = [p[1] for p in pz]
                tp["prize_cost"] = [p[2] for p in pz]
                words.append(tp.assign(tape_ref=None))
                fp = y[(y.y01 == 0) & y.s.notna() & (y.p0 < 0.90)]
                fps.append(fp[["event", "fam", "prov", "policy", "ticker", "title", "p0", "s"]])
                # NO-proxy per (L, V)
                for L in LS:
                    for V in VS:
                        for row in tp.itertuples():
                            if np.isnan(row.r) or abs(row.r) > MATCH_S:
                                continue
                            lead = L + row.r - row.asr_lat - V - 1.0
                            D = row.J_move - lead
                            end = row.Jh if (not np.isnan(row.Jh) and row.Jh > D) else D + 300
                            nc, ng = no_proxy(tape, row.ticker, D, end)
                            grid.append(dict(event=ev, fam=EVENTS[ev][1], prov=prov, policy=pol, L=L, V=V,
                                             ticker=row.ticker, lead=lead, win=lead > 0, no_ctr=nc, no_pnl=ng,
                                             prize_ctr=row.prize_ctr if lead > 0 else 0.0,
                                             prize_pnl=row.prize_gain if lead > 0 else 0.0))
            print(ev, prov, file=sys.stderr)
    W = pd.concat(words, ignore_index=True).drop(columns=["tape_ref"])
    FP = pd.concat(fps, ignore_index=True)
    G = pd.DataFrame(grid)
    W.to_csv(OUT / "lead_words.csv", index=False)
    FP.to_csv(OUT / "lead_fps.csv", index=False)
    G.to_csv(OUT / "lead_grid.csv", index=False)
    return W, FP, G


grid: list = []

if __name__ == "__main__":
    main()
