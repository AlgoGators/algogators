"""wx: settlement-source checks and publication timing.

1. Kalshi winner bracket (local settled_markets, NWS era) vs IEM-parsed NWS CLI value.
2. Kalshi expiration_value (fin_E /markets, 2026-08 -> 10, both NWS and TWC eras) vs NWS CLI.
3. Timing: Kalshi close -> settlement lag; NWS CLI issuance times (preliminary afternoon vs final next morning).
Writes data/vendor/wx/settle_check.csv, settle_timing.csv and prints a summary.
"""
from __future__ import annotations
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, ROOT, SERIES, STATIONS  # noqa: E402


def load_cli():
    d = pd.concat([pd.read_parquet(f) for f in glob.glob(str(OUT / "iem" / "cli" / "*.parquet"))], ignore_index=True)
    inv = {v[0]: k for k, v in STATIONS.items()}
    d["code"] = d.station.map(inv)
    d["date"] = d.valid
    for c in ("high", "low"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["prod_ts"] = pd.to_datetime(d["product"].str[:12], format="%Y%m%d%H%M", errors="coerce")
    return d[["code", "date", "high", "low", "prod_ts", "high_time", "low_time"]].drop_duplicates(["code", "date"])


def main():
    cli = load_cli()
    cli.to_parquet(OUT / "cli.parquet", index=False)
    m = pd.read_parquet(OUT / "markets.parquet")
    m["code"] = m.series.map(lambda s: SERIES[s][0]); m["measure"] = m.series.map(lambda s: SERIES[s][1])
    w = m[(m.win == 1) & m.lo.notna()].drop_duplicates("event_ticker")
    w = w.merge(cli, on=["code", "date"], how="left")
    w["v"] = np.where(w.measure == "high", w.high, w.low)
    w["agree"] = (w.v >= w.lo) & (w.v <= w.hi)
    w["kx"] = w.series.str.startswith("KX")
    print("== 1. Kalshi winning bracket vs NWS CLI (local settled markets)")
    print(w[w.v.notna()].groupby(["kx", "measure"]).agree.agg(["mean", "size"]))
    bad = w[w.v.notna() & ~w.agree]
    print("disagreements sample:\n", bad[["event_ticker", "lo", "hi", "v"]].head(15).to_string())
    w[["series", "event_ticker", "date", "code", "measure", "lo", "hi", "v", "agree", "close_ts", "settle_ts"]].to_csv(
        OUT / "settle_check.csv", index=False)

    print("== 2. fin_E expiration_value vs NWS CLI (Aug-Oct 2026)")
    f = pd.read_parquet(ROOT / "data" / "vendor" / "fin_E" / "kalshi_weather" / "markets.parquet")
    f = f.drop_duplicates("event")
    f["code"] = f.series.map(lambda s: SERIES.get(s, (None,))[0]); f["measure"] = f.series.map(lambda s: SERIES.get(s, (None, None))[1])
    f["date"] = pd.to_datetime(f.event.str.split("-").str[1], format="%y%b%d").dt.date.astype(str)
    f["xv"] = pd.to_numeric(f.expiration_value.astype(str).str.extract(r"(-?\d+(?:\.\d+)?)")[0], errors="coerce")
    f = f.merge(cli, on=["code", "date"], how="left")
    f["v"] = np.where(f.measure == "high", f.high, f.low)
    f["d"] = f.xv - f.v
    g = f[f.v.notna() & f.xv.notna()]
    print(g.groupby(["source", "measure"]).d.agg(n="size", same=lambda x: (x == 0).mean(), mean="mean",
                                                    absge1=lambda x: (x.abs() >= 1).mean()))
    print("diff distribution (TWC era):", g[g.source == "TWC"].d.value_counts().sort_index().to_dict())
    g[["series", "event", "date", "source", "measure", "xv", "v", "d"]].to_csv(OUT / "settle_twc_vs_nws.csv", index=False)

    print("== 3. timing")
    t = m.drop_duplicates("event_ticker").copy()
    t["lag_h"] = (t.settle_ts - t.close_ts) / 3600
    t["era"] = np.where(t.series.str.startswith("KX"), "KX", "legacy")
    print("close->settle lag hours by era:\n", t[t.lag_h.notna() & (t.lag_h > -48)].groupby("era").lag_h.describe(
        percentiles=[.1, .5, .9]))
    # settle time of day (UTC) & against CLI final product time
    tt = t.merge(cli[["code", "date", "prod_ts"]], on=["code", "date"], how="left")
    tt["settle_minus_cli_h"] = (pd.to_datetime(tt.settle_ts, unit="s") - tt.prod_ts).dt.total_seconds() / 3600
    print("settle - CLI(final) product time, hours:\n", tt.settle_minus_cli_h.describe(percentiles=[.1, .5, .9]))
    # AFOS: all CLI issuances
    af = [pd.read_parquet(x).assign(code=Path(x).stem.split("_")[0]) for x in glob.glob(str(OUT / "iem" / "afos" / "*.parquet"))]
    if af:
        a = pd.concat(af, ignore_index=True)
        a["prelim"] = a.valid.fillna("").str.contains("TODAY AS OF")
        a["hh"] = a.wmo_ddhhmm.str[2:4].astype(int)
        print("CLI issuances per type:", a.prelim.value_counts().to_dict())
        print("issue hour UTC, preliminary:", a[a.prelim].hh.value_counts().sort_index().to_dict())
        print("issue hour UTC, final:", a[~a.prelim].hh.value_counts().sort_index().to_dict())
        print("corrections share:", a.correction.mean())
        a.to_parquet(OUT / "afos_cli.parquet", index=False)
    tt[["series", "event_ticker", "date", "close_ts", "settle_ts", "prod_ts", "settle_minus_cli_h"]].to_csv(
        OUT / "settle_timing.csv", index=False)


if __name__ == "__main__":
    main()
