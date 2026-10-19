"""ASR offline replay evaluation (pre-registered in docs/ASR_LIVE_MENTION_DETECTION.md).

Steps per (event, provider):
  1. parse stream log -> final words + per-word interim/final latency
  2. grammar-match contract words causally (P-int, P-fin) -> detections
  3. market clock from the tape: p0, J_move, Jh per market
  4. align (release anchor; LOO spread), lead, paper P&L on taker-NO prints (proxy) + taker-YES competition
Outputs: data/vendor/asr/{latency,detections,words,fills,summary}*.csv
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter

import numpy as np
import pandas as pd

from asr_common import AUDIO, EVENTS, OUT, ROOT, TAPE
from asr_words import Detector, norm_tok

V_DELAYS = (0.0, 5.0, 15.0)
ORDER_LAT = 1.0
NOJH_WIN = 300.0


# ---------------------------------------------------------------- stream parsing
def load_stream(ev, prov):
    p = OUT / "stream" / f"{ev}__{prov}.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    if rows[-1].get("type") != "done":
        return None
    return rows


def messages(rows, prov):
    """Yield (rw, is_final, words[(tok, s, e, spk)]) where words is the hypothesis carried by the message.
    dg: interim/final segment hypotheses; aai: cumulative words of the current turn (final on end_of_turn)."""
    for r in rows:
        if prov == "dg" and r.get("type") == "R":
            yield r["rw"], bool(r["fin"]), [(norm_tok(w[0]), w[1], w[2], w[4]) for w in r["w"]]
        elif prov == "aai" and r.get("type") == "T":
            yield r["rw"], bool(r["eot"]), [(norm_tok(w[0]), w[1], w[2], None) for w in r["w"]]


def word_latency(rows, prov, t0):
    """Per final word: first-seen (interim) and final arrival latency vs the time its audio end was sent."""
    seen = {}  # (tok, round(s,1)) -> first rw
    fins = []
    for rw, fin, ws in messages(rows, prov):
        for tok, s, e, spk in ws:
            k = (tok, round(s, 1))
            if k not in seen:
                seen[k] = rw
        if fin:
            for tok, s, e, spk in ws:
                fins.append((tok, s, e, spk, rw))
    out = []
    dedup = set()
    for tok, s, e, spk, rw in fins:
        if (tok, round(s, 2)) in dedup or not tok:
            continue
        dedup.add((tok, round(s, 2)))
        first = min(seen.get((tok, round(s, 1)), rw), seen.get((tok, round(s - 0.1, 1)), rw),
                    seen.get((tok, round(s + 0.1, 1)), rw), rw)
        out.append(dict(tok=tok, s=s, e=e, spk=spk, lat_int=first - (t0 + e), lat_fin=rw - (t0 + e)))
    return pd.DataFrame(out)


# ---------------------------------------------------------------- detection
def detect(rows, prov, t0, markets, fed):
    """Causal detection via asr_words.Detector (same code path as the live harness)."""
    det = Detector(dict(zip(markets.ticker, markets.title)), speaker_filter=fed)
    out = []
    for rw, fin, ws in messages(rows, prov):
        for d in det.feed(rw, fin, ws):
            d["det_rw"] = d.pop("rw")
            d["asr_lat"] = d["det_rw"] - (t0 + d["e"])
            out.append(d)
    return pd.DataFrame(out)


# ---------------------------------------------------------------- market clock
def load_meta_markets(ev):
    f = "api_earnmention_markets" if "EARNINGS" in ev else "api_othermention_markets"
    m = pd.read_parquet(ROOT / "data/vendor/fin_D" / f"{f}.parquet",
                        columns=["event_ticker", "ticker", "yes_sub_title", "result"])
    m = m[m.event_ticker == ev].rename(columns={"yes_sub_title": "title"})
    return m[["ticker", "title", "result"]].reset_index(drop=True)


def load_tape(ev):
    t = pd.read_parquet(TAPE, columns=["ticker", "tus", "y", "tyes", "qty"], filters=[("event_ticker", "==", ev)])
    t["t"] = t.tus / 1e6
    return t.sort_values("t").reset_index(drop=True)


def market_clock(tape, S):
    rows = []
    for tk, g in tape.groupby("ticker"):
        pre = g[g.t < S]
        p0 = float(pre.y.iloc[-1]) if len(pre) else np.nan
        post = g[g.t >= S]
        lvl = max((p0 if not np.isnan(p0) else 0) + 0.15, 0.50)
        mv = post[post.y >= lvl]
        jmove = float(mv.t.iloc[0]) if len(mv) else np.nan
        # Jh: first print >= .95 with no later print < .90
        jh = np.nan
        y = post.y.values
        if len(y):
            later_min = np.minimum.accumulate(y[::-1])[::-1]
            ok = np.where((y >= 0.95) & (np.r_[later_min[1:], 1.0] >= 0.90))[0]
            if len(ok):
                jh = float(post.t.values[ok[0]])
        rows.append(dict(ticker=tk, p0=p0, J_move=jmove, Jh=jh))
    return pd.DataFrame(rows)


def taker_fee(p, c):
    return math.ceil(0.07 * c * p * (1 - p) * 100 - 1e-9) / 100


# ---------------------------------------------------------------- main
def first_speech_wall(lat, rel):
    return rel + lat.s.min() if len(lat) else np.nan


def run_event(ev, prov, sched):
    rows = load_stream(ev, prov)
    if rows is None:
        return None
    hdr = rows[0]
    t0 = hdr["t0_wall"]
    meta = json.loads((AUDIO / f"{ev}.json").read_text())
    rel = meta["release_timestamp"]
    fam = EVENTS[ev][1]
    lat = word_latency(rows, prov, t0)
    mk = load_meta_markets(ev)
    det = detect(rows, prov, t0, mk, fam == "fed")
    tape = load_tape(ev)
    S = sched - 120.0
    clk = market_clock(tape, S)
    w = mk.merge(clk, on="ticker", how="left")
    w["event"] = ev
    w["fam"] = fam
    w["prov"] = prov
    w["y01"] = (w.result == "yes").astype(int)
    out = []
    for pol in ("int", "fin"):
        d = det[det.policy == pol] if len(det) else pd.DataFrame(columns=["ticker"])
        x = w.merge(d, on="ticker", how="left")
        x["policy"] = pol
        x["spoken_wall"] = rel + x.s
        out.append(x)
    x = pd.concat(out, ignore_index=True)
    # chair 'good afternoon' / first speech check
    x["sched"] = sched
    x["first_speech_wall"] = first_speech_wall(lat, rel)
    lat["event"] = ev
    lat["prov"] = prov
    return x, lat, tape


def fills_for(x, tape, V):
    """Paper fills per detected market at viewer delay V."""
    recs = []
    for r in x[x.det_rw.notna()].itertuples():
        if not np.isnan(r.p0) and r.p0 >= 0.90:
            continue
        D = r.spoken_wall + r.asr_lat + V + ORDER_LAT
        end = r.Jh if (r.y01 == 1 and not np.isnan(r.Jh) and r.Jh > D) else D + NOJH_WIN
        g = tape[(tape.ticker == r.ticker) & (tape.t > D) & (tape.t <= end)]
        no = g[~g.tyes]
        ye = g[g.tyes]
        pnl = 0.0
        ctr = 0.0
        cost = 0.0
        for f in no.itertuples():
            fee = taker_fee(f.y, f.qty)
            pnl += f.qty * (r.y01 - f.y) - fee
            ctr += f.qty
            cost += f.qty * f.y + fee
        recs.append(dict(event=r.event, prov=r.prov, policy=r.policy, V=V, ticker=r.ticker, y01=r.y01, D=D,
                         lead=(r.J_move - D) if not np.isnan(r.J_move) else np.nan, no_ctr=ctr, cost=cost, pnl=pnl,
                         yes_taker_ctr=float(ye.qty.sum()), avg_y=(no.y * no.qty).sum() / ctr if ctr else np.nan))
    return pd.DataFrame(recs)


def loo_center(others, half=2.5):
    """Centre of the densest +-half window among the other words' d (robust to mismatched occurrences)."""
    o = np.asarray(others, float)
    o = o[~np.isnan(o)]
    if len(o) == 0:
        return np.nan
    cnt = [(np.sum(np.abs(o - v) <= half), -abs(v - np.median(o)), v) for v in o]
    v = max(cnt)[2]
    return float(np.median(o[np.abs(o - v) <= half]))


MATCH_S = 30.0


def boot_ci(per_event, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    v = np.asarray(per_event, float)
    if len(v) == 0:
        return (np.nan, np.nan)
    b = rng.choice(v, (n, len(v))).sum(1)
    return tuple(np.percentile(b, [2.5, 97.5]))


if __name__ == "__main__":
    sched = json.loads((OUT / "schedule.json").read_text())
    allw, alll, allf = [], [], []
    for ev in EVENTS:
        for prov in ("dg", "aai"):
            r = run_event(ev, prov, sched[ev])
            if r is None:
                continue
            x, lat, tape = r
            allw.append(x)
            alll.append(lat)
            for V in V_DELAYS:
                allf.append(fills_for(x, tape, V))
            print(ev, prov, "words", len(lat), file=sys.stderr)
    W = pd.concat(allw, ignore_index=True)
    L = pd.concat(alll, ignore_index=True)
    F = pd.concat(allf, ignore_index=True)
    W.to_csv(OUT / "words.csv", index=False)
    L.to_csv(OUT / "latency.csv", index=False)
    F.to_csv(OUT / "fills.csv", index=False)
    print("saved", len(W), len(L), len(F))
