"""NegRisk arbitrage episodes on Tardis free days (1st of each month, Jun-Oct 2026) at ms resolution.

RESEARCH ONLY. For each free day: rank event slugs by that day's traded USD (Tardis trades file already
reduced in data/vendor/tardis/trades/<date>.parquet + telonex markets_slim token map), look the top ones up
on gamma (negRisk flag + negRiskMarketID + all outcome YES tokens), keep negRisk groups with 2-12 outcomes,
take the top 20 by volume, and download the Tardis `quotes` (top-of-book change stream, us timestamps) for
every YES leg. Then merge legs as-of and compute top-of-book buy-all-YES and sell/convert edges net of
fees at every change -> episodes (edge > 0) with start, duration, max edge, top-of-book size.

Outputs data/vendor/negrisk/tardis/{quotes/<date>/<token20>.parquet, groups.parquet, points.parquet,
episodes.parquet}

    venv/Scripts/python.exe -I scripts/research/negrisk_tardis.py --dates 2026-06-01 ... [--fetch] [--analyse]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from negrisk_common import fee_per_share  # noqa: E402

OUT = ROOT / "data" / "vendor" / "negrisk" / "tardis"
TARDIS = ROOT / "data" / "vendor" / "tardis"
MARKETS = ROOT / "data" / "vendor" / "telonex" / "markets_slim.parquet"
BASE = "https://datasets.tardis.dev/v1/polymarket"
GAMMA = "https://gamma-api.polymarket.com"


def pick_groups(date: str, top_slugs: int = 80, keep: int = 20) -> pd.DataFrame:
    tr = pd.read_parquet(TARDIS / "trades" / f"{date}.parquet", columns=["symbol", "usd"])
    vol = tr.groupby("symbol", observed=True).usd.sum().rename("usd").reset_index()
    vol["symbol"] = vol.symbol.astype(str)
    m = pd.read_parquet(MARKETS, columns=["event_slug", "asset_id_0", "asset_id_1"])
    a = vol.merge(m, left_on="symbol", right_on="asset_id_0", how="inner")
    b = vol.merge(m, left_on="symbol", right_on="asset_id_1", how="inner")
    ev = pd.concat([a, b]).groupby("event_slug").usd.sum().sort_values(ascending=False)
    rows = []
    for slug in ev.index[:top_slugs]:
        try:
            e = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=30).json()
        except Exception:  # noqa: BLE001
            continue
        if not e or not e[0].get("negRisk"):
            continue
        e = e[0]
        grp = {}
        for mk in e.get("markets") or []:
            if not isinstance(mk, dict) or not mk.get("clobTokenIds"):
                continue
            grp.setdefault(mk.get("negRiskMarketID") or "x", []).append(mk)
        for nr, mks in grp.items():
            # outcomes alive on `date`: not closed before the day started
            day0 = pd.Timestamp(date, tz="UTC")
            alive = []
            for mk in mks:
                ct = mk.get("closedTime")
                try:
                    ctt = pd.Timestamp(ct) if ct else None
                    if ctt is not None and ctt.tzinfo is None:
                        ctt = ctt.tz_localize("UTC")
                except ValueError:
                    ctt = None
                if ctt is not None and ctt < day0:
                    continue
                alive.append(mk)
            if not (2 <= len(alive) <= 12):
                continue
            fs = [(mk.get("feeSchedule") or {}) for mk in alive]
            rows.append(dict(date=date, slug=slug, nr_id=nr, n=len(alive), usd=float(ev[slug]),
                             tokens=[json.loads(mk["clobTokenIds"])[0] for mk in alive],
                             rates=[float(f.get("rate") or 0) if mk.get("feesEnabled") else 0.0
                                    for f, mk in zip(fs, alive)]))
        time.sleep(0.1)
        if len(rows) >= keep:
            break
    return pd.DataFrame(rows)


def fetch_quotes(date: str, tok: str, tmp: Path) -> int:
    dest = OUT / "quotes" / date / f"{tok[:20]}.parquet"
    if dest.exists():
        return -1
    dest.parent.mkdir(parents=True, exist_ok=True)
    y, mo, d = date.split("-")
    raw = tmp / f"{tok[:20]}.csv.gz"
    for i in range(4):
        try:
            req = urllib.request.Request(f"{BASE}/quotes/{y}/{mo}/{d}/{tok}.csv.gz",
                                         headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(req, timeout=300) as r, open(raw, "wb") as f:
                f.write(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                pd.DataFrame(columns=["timestamp", "ask_amount", "ask_price", "bid_price", "bid_amount"]).to_parquet(dest)
                return 0
            time.sleep(3 * (i + 1))
        except Exception:  # noqa: BLE001
            time.sleep(3 * (i + 1))
    else:
        return 0
    try:
        q = pd.read_csv(raw, usecols=["timestamp", "ask_amount", "ask_price", "bid_price", "bid_amount"])
    except (pd.errors.EmptyDataError, ValueError):
        q = pd.DataFrame(columns=["timestamp", "ask_amount", "ask_price", "bid_price", "bid_amount"])
    q.to_parquet(dest, index=False, compression="zstd")
    raw.unlink(missing_ok=True)
    return len(q)


def analyse(G: pd.DataFrame):
    pts, eps = [], []
    for g in G.itertuples():
        legs = []
        for tok in g.tokens:
            fp = OUT / "quotes" / g.date / f"{tok[:20]}.parquet"
            if not fp.exists():
                break
            q = pd.read_parquet(fp)
            if q.empty:
                break
            q = q.groupby("timestamp").tail(1).sort_values("timestamp")
            legs.append(q)
        if len(legs) != len(g.tokens):
            continue
        rates = np.array(g.rates)
        t_all = np.unique(np.concatenate([q.timestamp.values for q in legs]))
        t_all = t_all[t_all >= max(q.timestamp.values[0] for q in legs)]
        t_all = t_all[t_all <= min(q.timestamp.values[-1] for q in legs)]  # a leg that stops quoting has closed
        if len(t_all) < 2:
            continue
        A = np.full((len(t_all), len(legs)), np.nan)
        B, AS, BS = A.copy(), A.copy(), A.copy()
        for k, q in enumerate(legs):
            ix = np.searchsorted(q.timestamp.values, t_all, side="right") - 1
            A[:, k] = q.ask_price.values[ix]
            B[:, k] = q.bid_price.values[ix]
            AS[:, k] = q.ask_amount.values[ix]
            BS[:, k] = q.bid_amount.values[ix]
        fa = rates * A * (1 - A)
        fb = rates * B * (1 - B)
        buy = 1 - np.nansum(A + fa, axis=1)
        buy[np.isnan(A).any(axis=1)] = np.nan
        nb = B - fb
        nbz = np.where(np.isnan(nb), -1, nb)
        pos = np.where(nbz > 0, nbz, 0).sum(axis=1)
        two = -np.sort(-nbz, axis=1)[:, :2].sum(axis=1)
        sell = np.where((nbz > 0).sum(axis=1) >= 2, pos, two) - 1
        buy_sz = np.nanmin(AS, axis=1)
        sell_sz = np.where(nbz > 0, BS, np.inf).min(axis=1)
        dur_next = np.diff(np.append(t_all, t_all[-1])) / 1000.0  # ms until next change
        for side, edge, sz in (("buy", buy, buy_sz), ("sell", sell, sell_sz)):
            on = np.nan_to_num(edge, nan=-1) > 0
            if not on.any():
                continue
            # episodes = maximal runs of on
            d = np.diff(np.concatenate([[0], on.astype(int), [0]]))
            st, en = np.where(d == 1)[0], np.where(d == -1)[0]
            for s, e in zip(st, en):
                t0, t1 = t_all[s], t_all[e] if e < len(t_all) else t_all[-1]
                eps.append(dict(date=g.date, slug=g.slug, nr_id=g.nr_id, n=g.n, side=side,
                                t0_us=int(t0), dur_ms=(t1 - t0) / 1000.0, max_edge=float(np.nanmax(edge[s:e])),
                                edge0=float(edge[s]), size0=float(sz[s]),
                                profit_top=float(np.nanmax(edge[s:e] * np.minimum(sz[s:e], 1e9))),
                                n_updates=int(e - s)))
        pts.append(dict(date=g.date, slug=g.slug, nr_id=g.nr_id, n=g.n, n_changes=len(t_all),
                        sum_ask_med=float(np.nanmedian(np.nansum(A, axis=1))),
                        buy_edge_max=float(np.nanmax(buy)) if np.isfinite(buy).any() else np.nan,
                        sell_edge_max=float(np.nanmax(sell)),
                        frac_time_buy_on=float(np.nansum(dur_next[np.nan_to_num(buy, nan=-1) > 0]) /
                                               max(np.sum(dur_next), 1)),
                        frac_time_sell_on=float(np.nansum(dur_next[sell > 0]) / max(np.sum(dur_next), 1))))
    P, E = pd.DataFrame(pts), pd.DataFrame(eps)
    P.to_parquet(OUT / "groups_stats.parquet", index=False)
    E.to_parquet(OUT / "episodes.parquet", index=False)
    print("groups analysed", len(P), "episodes", len(E))
    if len(E):
        print(E.groupby(["date", "side"]).agg(n=("dur_ms", "size"), med_ms=("dur_ms", "median"),
                                              max_edge=("max_edge", "max")).to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", default=["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01",
                                                   "2026-10-01"])
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--analyse", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    gf = OUT / "groups.parquet"
    if a.fetch:
        G = pd.concat([pick_groups(d) for d in a.dates], ignore_index=True)
        G.to_parquet(gf, index=False)
        print(G.groupby("date").agg(groups=("slug", "size"), legs=("n", "sum")).to_string(), flush=True)
        tmp = Path(tempfile.mkdtemp(prefix="nr_tardis_"))
        jobs = [(g.date, t) for g in G.itertuples() for t in g.tokens]
        with ThreadPoolExecutor(6) as ex:
            n = list(ex.map(lambda j: fetch_quotes(j[0], j[1], tmp), jobs))
        print("quote files", len(jobs), "rows", sum(x for x in n if x > 0), flush=True)
    G = pd.read_parquet(gf)
    if a.analyse:
        analyse(G)


if __name__ == "__main__":
    main()
