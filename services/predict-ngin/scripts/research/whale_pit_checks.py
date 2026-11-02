"""whale_pit leakage checks and score-persistence test.

L1  brute-force as-of: for 500 random signals, recompute each wallet's mk/res sums from
    events.parquet with ``k < ts`` and compare with the ASOF-joined values in signals.parquet.
L2  timing invariants: every mk event is known 24h after its trade; every res event is known at
    least one day after the market's last lake fill and after its scheduled end; no signal uses an
    event whose known time is >= the signal time.
P   persistence: does the PIT score rank forward returns at all? Test-period signals (>= $1k,
    >= 20 prior scored trades, one per market/side/day), resolution return per $ at the whale's own
    price (no delay, no cost), by score decile; top-minus-bottom decile with an event-clustered CI.

    uv run --no-sync python scripts/research/whale_pit_checks.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from predict_ngin.shared.duck import connect, sql_path
from predict_ngin.shared.paths import data_root

# Data end (exclusive). Default 2026-01-01 = the original study; WHALE_PIT_END=2026-10-17 runs the
# 2026 extension into its own folder (data/research/whale_pit/end_2026-10-17), leaving the original intact.
END = os.environ.get("WHALE_PIT_END", "2026-01-01")
DATA_END_TS = int(datetime.fromisoformat(END).replace(tzinfo=timezone.utc).timestamp())
_ORIG = END == "2026-01-01"
OUT = data_root() / "research" / "whale_pit" / ("" if _ORIG else f"end_{END}")
RNG = np.random.default_rng(3)


def main() -> int:
    con = connect(memory="2GB", threads=2)
    sig, ev, mk = (sql_path(OUT / f) for f in ("signals.parquet", "events.parquet", "markets.parquet"))
    res = {}

    # L1 -----------------------------------------------------------------------------------------
    con.execute(f"CREATE TEMP TABLE smp AS SELECT w, ts, mk_u, mk_p, mk_n, res_u, res_p, res_n "
                f"FROM read_parquet({sig}) USING SAMPLE 500 ROWS (reservoir, 7)")
    bf = con.sql(f"""
        SELECT s.w, s.ts, any_value(s.mk_u) mk_u, any_value(s.mk_p) mk_p, any_value(s.mk_n) mk_n,
               any_value(s.res_u) res_u, any_value(s.res_p) res_p, any_value(s.res_n) res_n,
               sum(e.usd) FILTER (WHERE e.kind = 'mk') bf_mk_u, sum(e.pnl) FILTER (WHERE e.kind = 'mk') bf_mk_p,
               count(e.k) FILTER (WHERE e.kind = 'mk') bf_mk_n,
               sum(e.usd) FILTER (WHERE e.kind = 'res') bf_res_u, sum(e.pnl) FILTER (WHERE e.kind = 'res') bf_res_p,
               count(e.k) FILTER (WHERE e.kind = 'res') bf_res_n
        FROM smp s LEFT JOIN read_parquet({ev}) e ON e.w = s.w AND e.k < s.ts
        GROUP BY s.w, s.ts""").df()
    for k in ("mk", "res"):
        for c in ("u", "p", "n"):
            a, b = bf[f"{k}_{c}"].fillna(0).to_numpy(float), bf[f"bf_{k}_{c}"].fillna(0).to_numpy(float)
            res[f"L1_{k}_{c}_max_abs_diff"] = float(np.max(np.abs(a - b) / np.maximum(1, np.abs(b))))
    res["L1_signals_checked"] = len(bf)

    # L2 -----------------------------------------------------------------------------------------
    res["L2_signal_uses_future_event"] = int(con.sql(
        f"SELECT count(*) FROM read_parquet({sig}) WHERE mk_k >= ts OR res_k >= ts").fetchone()[0])
    res["L2_markets_known_before_last_fill_plus_1d"] = int(con.sql(
        f"SELECT count(*) FROM read_parquet({mk}) WHERE known_ts < last_fill_ts + 86400 "
        f"OR known_ts < coalesce(end_ts, 0) + 86400").fetchone()[0])
    res["L2_min_res_lag_after_trade_days"] = float(con.sql(f"""
        SELECT min(m.known_ts - t.ts) / 86400 FROM read_parquet({sql_path(OUT / 'trades' / '*.parquet')}) t
        JOIN read_parquet({mk}) m USING (market_id) WHERE m.known_ts < 1e18""").fetchone()[0])

    # P ------------------------------------------------------------------------------------------
    s = con.sql(f"""
        SELECT *, ((CASE WHEN d = 1 THEN yes_payoff ELSE 1 - yes_payoff END)
                   - CASE WHEN d = 1 THEN p_vwap ELSE 1 - p_vwap END) / CASE WHEN d = 1 THEN p_vwap ELSE 1 - p_vwap END AS r,
               CASE WHEN d = 1 THEN p_vwap ELSE 1 - p_vwap END AS c0,
               mk_p / (mk_u + 20000) AS S_mk, res_p / (res_u + 20000) AS S_res
        FROM read_parquet({sig})
        WHERE ts >= 1735689600 AND yes_payoff IS NOT NULL""").df()
    s["day"] = (s["ts"] // 86400).astype(int)
    for k in ("mk", "res"):
        x = (s[(s[f"{k}_n"] >= 20) & s["c0"].between(0.05, 0.95)]
             .sort_values(["ts", "market_id", "w", "d", "usd"], kind="mergesort")
             .drop_duplicates(["market_id", "d", "day"]))
        x = x.assign(dec=pd.qcut(x[f"S_{k}"].rank(method="first"), 10, labels=False))
        tab = x.groupby("dec").agg(n=("r", "size"), S_lo=(f"S_{k}", "min"), S_hi=(f"S_{k}", "max"), mean_r=("r", "mean"))
        top, bot = x[x["dec"] == 9], x[x["dec"] == 0]
        diffs = []
        te, be = top.groupby("event_id")["r"].agg(["sum", "size"]), bot.groupby("event_id")["r"].agg(["sum", "size"])
        for _ in range(2000):
            a = te.iloc[RNG.integers(0, len(te), len(te))].sum()
            b = be.iloc[RNG.integers(0, len(be), len(be))].sum()
            diffs.append(a["sum"] / a["size"] - b["sum"] / b["size"])
        res[f"P_{k}"] = dict(n=len(x), spearman=float(x[[f"S_{k}", "r"]].corr("spearman").iloc[0, 1]),
                             top_minus_bottom=float(top["r"].mean() - bot["r"].mean()),
                             ci=[float(np.quantile(diffs, .025)), float(np.quantile(diffs, .975))],
                             deciles=tab.round(4).reset_index().to_dict("records"))
    # F ------------------------------------------------------------------------------------------
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import whale_pit_eval as E

    a = E.load()
    tr, te = a[E.in_window(a, E.TRAIN)], a[E.in_window(a, E.TEST)]
    cut = float(tr.loc[(tr["usd"] >= 1000) & (tr["mk_n"] >= 20), "S_mk"].quantile(0.10))
    res["F_train_cut"] = cut
    for nm, w in (("train", tr), ("test", te)):
        rows = E.dedupe(w, ((w["usd"] >= 1000) & (w["mk_n"] >= 20) & (w["S_mk"] <= cut)).to_numpy())
        side = -rows["d"].to_numpy()
        for ex in ("res", "24h"):
            for lab, kw in (("realistic", {}), ("gross_at_whale", dict(at_whale=True)), ("no_fee", dict(fees=False))):
                res[f"F_{nm}_{ex}_{lab}"], _ = E.evaluate(w, rows, side=side, exit_=ex, **kw)
    print(json.dumps(res, indent=1, default=float))
    (OUT / "checks.json").write_text(json.dumps(res, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
