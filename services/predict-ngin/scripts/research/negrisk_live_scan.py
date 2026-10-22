"""Live REST snapshot scan of Polymarket NegRisk events for intra-venue arbitrage (RESEARCH ONLY).

Every ``--interval`` seconds: POST /books (public) for the YES token of every open market of every open
negRisk event in data/vendor/negrisk/universe_latest.parquet, then per event compute
  * buy-all-YES: sum of best asks, depth-walked net-of-fee profit / size / capital (only if the event is
    complete: every unresolved market tradable),
  * sell side (buy NOs + convert): best subset of YES bids, depth-walked net-of-fee profit / capital,
  * profit attainable at $1k / $2.5k / $5k / $10k of capital per trade.
Also, every 10th sweep, fetches YES and NO books of a sample of markets to check the mirror property
(ask_NO == 1 - bid_YES) and count crossed books.

Output: data/vendor/negrisk/live/sweeps.parquet-parts (one parquet per sweep) and ladders_*.jsonl for
events within 1c of an arb.

    venv/Scripts/python.exe scripts/research/negrisk_live_scan.py --minutes 100 --interval 60
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from negrisk_common import buy_all_yes, fee_per_share, parse_levels, sell_all_yes  # noqa: E402

CLOB = "https://clob.polymarket.com"
OUT = Path("data/vendor/negrisk/live")
UNI = Path("data/vendor/negrisk/universe_latest.parquet")
CAPS = (1000, 2500, 5000, 10000)


def resolved_yes(prices) -> bool:
    try:
        p = json.loads(prices) if isinstance(prices, str) else prices
        return float(p[0]) >= 0.99
    except Exception:  # noqa: BLE001
        return False


def _post(s, chunk):
    for attempt in range(4):
        try:
            r = s.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk], timeout=30)
            if r.status_code == 429:
                time.sleep(1 + attempt)
                continue
            r.raise_for_status()
            return r.json() or []
        except Exception:  # noqa: BLE001
            time.sleep(1 + attempt)
    return []


def fetch_books(s: requests.Session, tokens, batch=500, workers=6):
    """Batch POST /books, `workers` requests in flight (limit is 500 req / 10 s)."""
    from concurrent.futures import ThreadPoolExecutor
    chunks = [tokens[i:i + batch] for i in range(0, len(tokens), batch)]
    out = {}
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(lambda c: _post(s, c), chunks):
            for b in res:
                out[str(b.get("asset_id"))] = b
    return out


def load_universe():
    u = pd.read_parquet(UNI)
    u = u[u.yes_token.notna()]
    return u, UNI.stat().st_mtime


def analyse_event(g: pd.DataFrame, books: dict, t_recv: float):
    """g: markets of one event (all unresolved ones). Returns (row dict, ladders dict or None)."""
    open_m = g[~g.closed]
    if len(open_m) < 2:
        return None, None
    if g[g.closed].outcome_prices.map(resolved_yes).any():
        return None, None  # an outcome already resolved YES: event is over
    asks, bids, rates, exps, ts = [], [], [], [], []
    complete = True
    for _, m in open_m.iterrows():
        b = books.get(m.yes_token)
        if not m.accepting or b is None:
            complete = False
            asks.append([]); bids.append([])
        else:
            asks.append(parse_levels(b.get("asks")))
            bids.append(parse_levels(b.get("bids")))
            try:
                ts.append(int(b.get("timestamp")))
            except (TypeError, ValueError):
                pass
            if not asks[-1]:
                complete = False
        rates.append(float(m.fee_rate)); exps.append(float(m.fee_exp))
    best_ask = [min(a)[0] if a else None for a in asks]
    best_bid = [max(b)[0] if b else None for b in bids]
    row = dict(
        event_id=g.event_id.iloc[0], nr_id=g.nr_id.iloc[0], event_slug=g.event_slug.iloc[0], n_open=len(open_m),
        n_with_ask=sum(a is not None for a in best_ask), n_with_bid=sum(b is not None for b in best_bid),
        complete=complete, augmented=bool(g.neg_risk_augmented.iloc[0]), tags=g.tags.iloc[0],
        fee_rate=max(rates), event_end=g.event_end.iloc[0], vol24h=float(g.event_volume24h.iloc[0]),
        sum_ask=sum(a for a in best_ask if a is not None) if complete else None,
        sum_bid=sum(b for b in best_bid if b is not None),
        book_ts_spread_ms=(max(ts) - min(ts)) if ts else None, t=t_recv,
    )
    # buy side
    if complete:
        f = buy_all_yes(asks, rates, exponents=exps, min_shares=5)
        row.update(buy_top_edge=f.top_edge, buy_shares=f.shares, buy_profit=f.profit, buy_capital=f.capital)
        for c in CAPS:
            row[f"buy_profit_{c}"] = f.at_capital(c)[1]
    # sell side: legs with a bid; choose prefix of legs sorted by net bid that maximises depth-walked profit
    legs = [(best_bid[i] - fee_per_share(best_bid[i], rates[i], exps[i]), i) for i in range(len(bids)) if bids[i]]
    legs.sort(reverse=True)
    S = [i for v, i in legs if v > 0]
    if len(S) < 2:
        S = [i for _, i in legs[:2]]
    if len(S) >= 2:
        f = sell_all_yes([bids[i] for i in S], [rates[i] for i in S], exponents=[exps[i] for i in S], min_shares=5)
        row.update(sell_k=len(S), sell_top_edge=f.top_edge, sell_shares=f.shares, sell_profit=f.profit,
                   sell_capital=f.capital)
        for c in CAPS:
            row[f"sell_profit_{c}"] = f.at_capital(c)[1]
    lad = None
    near = (row.get("buy_top_edge") is not None and row.get("buy_top_edge", -1) > -0.01) or \
           (row.get("sell_top_edge") is not None and row.get("sell_top_edge", -1) > -0.01)
    if near:
        lad = dict(event_id=row["event_id"], nr_id=row["nr_id"], t=t_recv, tokens=list(open_m.yes_token), rates=rates,
                   asks=[a[:10] for a in asks], bids=[sorted(b, key=lambda x: -x[0])[:10] for b in bids],
                   ts=ts)
    return row, lad


def mirror_check(s, u, k=200):
    """Fetch YES and NO books of k random tradable markets; measure ask_YES + ask_NO and crossing."""
    m = u[u.accepting & ~u.closed & u.no_token.notna()]
    m = m.sample(min(k, len(m)), random_state=random.randint(0, 10**6))
    books = fetch_books(s, list(m.yes_token) + list(m.no_token))
    res = []
    for _, r in m.iterrows():
        y, n = books.get(r.yes_token), books.get(r.no_token)
        if not y or not n:
            continue
        ya, yb = parse_levels(y.get("asks")), parse_levels(y.get("bids"))
        na = parse_levels(n.get("asks"))
        if ya and na and yb:
            res.append(dict(sum_asks=min(ya)[0] + min(na)[0], mirror_err=abs(min(na)[0] - (1 - max(yb)[0])),
                            crossed=max(yb)[0] >= min(ya)[0]))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=100)
    ap.add_argument("--interval", type=float, default=60)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    u, mt = load_universe()
    t_end = time.time() + a.minutes * 60
    sweep = 0
    mirror_rows = []
    while time.time() < t_end:
        t0 = time.time()
        if UNI.stat().st_mtime != mt:
            u, mt = load_universe()
        live = u[~u.closed]
        books = fetch_books(s, list(live.yes_token.unique()))
        t_recv = time.time()
        rows, lads = [], []
        for _, g in u.groupby("nr_id"):
            row, lad = analyse_event(g, books, t_recv)
            if row:
                row["sweep"] = sweep
                rows.append(row)
            if lad:
                lads.append(lad)
        df = pd.DataFrame(rows)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        df.to_parquet(OUT / f"sweep_{stamp}.parquet", index=False)
        if lads:
            with gzip.open(OUT / f"ladders_{stamp}.jsonl.gz", "wt") as f:
                for l in lads:
                    f.write(json.dumps(l) + "\n")
        if sweep % 10 == 0:
            mr = mirror_check(s, u)
            for r in mr:
                r["sweep"] = sweep
            mirror_rows += mr
            pd.DataFrame(mirror_rows).to_parquet(OUT / "mirror_check.parquet", index=False)
        nb = int((df.get("buy_top_edge", pd.Series(dtype=float)) > 0).sum())
        ns = int((df.get("sell_top_edge", pd.Series(dtype=float)) > 0).sum())
        print(f"{stamp} sweep {sweep}: events {len(df)} books {len(books)} fetch {t_recv - t0:.1f}s "
              f"buy_arbs {nb} sell_arbs {ns}", flush=True)
        sweep += 1
        time.sleep(max(0.0, a.interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
