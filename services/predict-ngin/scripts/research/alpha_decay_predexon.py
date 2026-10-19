"""Quote-based cross-check of 2026 cross-venue decay on Predexon book history (Jan-Oct 2026, 7,543 tasks).

RESEARCH ONLY; reads data/vendor/predexon/books (final, read-only; written by another agent) and never calls the
Predexon API. Polymarket rows are sparse snapshots (seconds to minutes apart), so - as in predexon_book_arb.py -
everything is evaluated only at Polymarket snapshot instants with the Kalshi book as-of (PM-anchored, exact).

Per pair and month (games pre-game only: snapshot ts < scheduled start; futures/Fed: whole task window):
  share of PM snapshot instants with a gross cross (K ask < PM bid or PM ask < K bid), a net cross under today's
  fees (Kalshi 0.07, Polymarket 0.05: "const") and under the market's own fee (0 if disabled: "actual"), and the
  standing net $ at the touch (edge x min touch size).
Outputs data/vendor/alpha_decay/predexon/pair_month.parquet

    venv/Scripts/python.exe scripts/research/alpha_decay_predexon.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "vendor" / "predexon"
OUT = ROOT / "data" / "vendor" / "alpha_decay" / "predexon"


def kf(p):
    return 0.07 * p * (1 - p)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    P = pd.read_parquet(D / "pairs.parquet").drop_duplicates("pair").set_index("pair")
    rows = []
    with open(D / "books" / "manifest.jsonl") as fh:
        man = [json.loads(x) for x in fh if x.strip()]
    for i, m in enumerate(man):
        if m["pair"] not in P.index or not m.get("kalshi_file") or not m.get("polymarket_file"):
            continue
        fk, fp = D / "books" / "kalshi" / m["kalshi_file"], D / "books" / "polymarket" / m["polymarket_file"]
        if not fk.exists() or not fp.exists():
            continue
        r = P.loc[m["pair"]]
        k = pd.read_parquet(fk, columns=["ts", "b1", "bs1", "a1", "as1"]).sort_values("ts")
        p = pd.read_parquet(fp, columns=["ts", "b1", "bs1", "a1", "as1"]).sort_values("ts")
        if k.empty or p.empty:
            continue
        gs = pd.to_datetime(r.game_start, utc=True, errors="coerce")
        if pd.notna(gs):
            cut = gs.timestamp() * 1000
            p = p[p.ts < cut]
        if p.empty:
            continue
        j = np.searchsorted(k.ts.values, p.ts.values, side="right") - 1
        ok = j >= 0
        p, j = p[ok], j[ok]
        if p.empty:
            continue
        Kb, Ka, Kbs, Kas = (k[c].values[j] for c in ("b1", "a1", "bs1", "as1"))
        Pb, Pa, Pbs, Pas = p.b1.values, p.a1.values, p.bs1.values, p.as1.values
        rate_act = float(r.pm_fee_rate) if bool(r.pm_fees_enabled) and pd.notna(r.pm_fee_rate) else 0.0
        valid = (np.nan_to_num(Ka) >= 0.02) & (np.nan_to_num(Ka) <= 0.98) & (np.nan_to_num(Pa) >= 0.02) \
            & (np.nan_to_num(Pa) <= 0.98) & np.isfinite(Kb) & np.isfinite(Pb)
        g1, g2 = Pb - Ka, Kb - Pa
        out = dict(pair=m["pair"], family=m["family"], month=m["month"], snaps=int(valid.sum()),
                   k_volume=float(r.k_volume or 0), pm_volume=float(r.pm_volume or 0))
        gross = valid & ((g1 > 0) | (g2 > 0))
        out["gross"] = int(gross.sum())
        for nm, rate in (("const", 0.05), ("actual", rate_act)):
            e1 = g1 - kf(Ka) - rate * Pb * (1 - Pb)
            e2 = g2 - kf(Kb) - rate * Pa * (1 - Pa)
            e = np.fmax(np.nan_to_num(e1, nan=-1), np.nan_to_num(e2, nan=-1))
            hit = valid & (e > 0)
            sz = np.where(np.nan_to_num(e1, nan=-1) >= np.nan_to_num(e2, nan=-1), np.fmin(Kas, Pbs), np.fmin(Kbs, Pas))
            out[nm] = int(hit.sum())
            out[nm + "_usd"] = float(np.nansum(np.where(hit, e * np.nan_to_num(sz), 0.0)))
        rows.append(out)
        if i % 500 == 0:
            print(f"{i}/{len(man)}", flush=True)
    pd.DataFrame(rows).to_parquet(OUT / "pair_month.parquet", index=False)
    print(len(rows), "pair-tasks")


if __name__ == "__main__":
    sys.exit(main())
