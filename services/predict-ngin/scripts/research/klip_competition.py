"""LIP reward share a small maker would earn, from the live snapshots (klip_snapshot_books.py).

For every active (programme, market, side) and every snapshot round, inserts ONE of our resting bids and scores it
with klip_lip.side_score against the actual resting book (= the competition). Placements:
  best    - join behind the current best bid
  improve - best bid + 1c, alone at the front (only if it does not lock/cross the other side)
  ref     - join behind the deepest level that still earns the full 1.0 multiplier (reference price level)
Sizes q (contracts). Scenarios: base, comp2 (every resting level doubled), rival3 (3 rivals of our size at our
price, ahead of us in time priority).
Output: data/vendor/klip/analysis/shares_<round>.parquet (one row per market x side x placement x q x scenario).
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import klip_lip as L  # noqa: E402

SNAP = ROOT / "data" / "vendor" / "klip" / "snapshots"
OUT = ROOT / "data" / "vendor" / "klip" / "analysis"
QS = [10, 25, 50, 100, 250, 500, 1000]


def ref_level(px, sz, target):
    order = np.argsort(-px)
    cum = 0.0
    for i in order:
        cum += sz[i]
        if cum >= target / 5.0:
            return px[i]
    return px[order[-1]] if len(px) else np.nan


def analyse_round(stamp: str, mode: str = "level") -> pd.DataFrame:
    pr = pd.read_parquet(SNAP / f"programs_{stamp}.parquet")
    bk = pd.read_parquet(SNAP / f"books_{stamp}.parquet")
    for c in ("start_date", "end_date"):
        pr[c] = pd.to_datetime(pr[c], format="ISO8601", utc=True)
    pr["usd"] = pr.period_reward / 1e4
    pr["dur_d"] = (pr.end_date - pr.start_date).dt.total_seconds() / 86400
    pr["pool_day"] = pr.usd / pr.dur_d.clip(lower=1 / 96)
    pr["tgt"] = pr.target_size_fp.astype(float)
    pr["df"] = pr.discount_factor_bps / 1e4
    books = {t: g for t, g in bk[bk.side != "empty"].groupby("ticker")}
    rows = []
    for r in pr.itertuples():
        g = books.get(r.market_ticker)
        yes = (g[g.side == "yes"].price.to_numpy(), g[g.side == "yes"]["size"].to_numpy()) if g is not None else (np.array([]), np.array([]))
        no = (g[g.side == "no"].price.to_numpy(), g[g.side == "no"]["size"].to_numpy()) if g is not None else (np.array([]), np.array([]))
        best = {"yes": yes[0].max() if len(yes[0]) else np.nan, "no": no[0].max() if len(no[0]) else np.nan}
        tot = {"yes": yes[1].sum(), "no": no[1].sum()}
        base = dict(snap=stamp, ticker=r.market_ticker, prog=r.id, desc=r.incentive_description, pool_day=r.pool_day,
                    tgt=r.tgt, df=r.df, end=r.end_date, yes_best=best["yes"], no_best=best["no"],
                    yes_tot=tot["yes"], no_tot=tot["no"],
                    qualifies=bool(tot["yes"] >= r.tgt and tot["no"] >= r.tgt))
        for side, (px, sz), opp in (("yes", yes, no), ("no", no, yes)):
            opp_best = opp[0].max() if len(opp[0]) else 0.0
            implied_ask = 1.0 - opp_best  # our side's lowest ask
            plc = {}
            if len(px):
                plc["best"] = round(px.max(), 2)
                imp = round(px.max() + 0.01, 2)
                if imp < implied_ask - 1e-9 and imp <= 0.99:
                    plc["improve"] = imp
                plc["ref"] = round(ref_level(px, sz, r.tgt), 2)
            else:
                plc["improve"] = 0.01 if implied_ask > 0.01 + 1e-9 else None  # empty side: sit at 1c
            for name, p in plc.items():
                if p is None or not np.isfinite(p) or p <= 0:
                    continue
                for q in QS:
                    for scen in ("base", "comp2", "rival3"):
                        spx, ssz = px, sz
                        if scen == "comp2":
                            ssz = sz * 2
                        if scen == "rival3":
                            spx = np.concatenate([px, [p]]); ssz = np.concatenate([sz, [3 * q]])
                        ok, o, t = L.side_score(spx, ssz, r.tgt, r.df, p, q, mode=mode)
                        rows.append({**base, "side": side, "placement": name, "px": p, "q": q, "scen": scen,
                                     "side_ok": ok, "share": (o / t) if t > 0 else 0.0,
                                     "capital": q * p, "dist_to_ask_c": round(100 * (implied_ask - p))})
    return pd.DataFrame(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stamps = sorted(Path(f).stem.split("_", 1)[1] for f in glob.glob(str(SNAP / "books_*.parquet")))
    if len(sys.argv) > 1:
        stamps = [s for s in stamps if s in sys.argv[1:]]
    for s in stamps:
        p = OUT / f"shares_{s}.parquet"
        if p.exists():
            continue
        df = analyse_round(s)
        df.to_parquet(p, index=False)
        print(s, len(df), flush=True)


if __name__ == "__main__":
    main()
