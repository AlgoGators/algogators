"""wx: extract Kalshi daily-temperature trades from the local tape (full tape via
predict_ngin.tapes.kalshi, read-only)
and build the per-market table (strikes parsed from tickers, results from settled_markets).

Ticker conventions (checked against /markets floor/cap in fin_E markets.parquet, see `check`):
  -B<x.5>  bracket: YES iff value in [x-0.5, x+0.5] i.e. the two integers floor(x), floor(x)+1
  -T<k>    tail: the larger T strike of an event is 'greater' (YES iff value > k, i.e. >= k+1),
                 the smaller is 'less' (YES iff value < k, i.e. <= k-1)
Outputs data/vendor/wx/tape.parquet, data/vendor/wx/markets.parquet
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, ROOT, SERIES  # noqa: E402
sys.path.insert(0, str(ROOT))
from predict_ngin.shared.duck import connect  # noqa: E402
from predict_ngin.tapes.kalshi import tape_sql  # noqa: E402

RX = r"^(KX)?(HIGH|LOW)"


def extract():
    parts = []
    con = connect()
    # full tape via predict_ngin.tapes.kalshi (trades_fixed alone ends 2026-01-27), one UTC month at a time
    for p in pd.period_range("2021-06", pd.Timestamp.now(tz="UTC").strftime("%Y-%m"), freq="M"):
        m0, m1 = (int(x.tz_localize("UTC").timestamp()) for x in (p.start_time, (p + 1).start_time))
        t = con.sql(tape_sql(["ticker", "yes_price", "count", "taker_side", "timestamp_unix"], m0, m1)).to_arrow_table()
        t = t.filter(pc.match_substring_regex(t["ticker"], RX))
        if t.num_rows == 0:
            continue
        d = t.to_pandas()
        d["series"] = d.ticker.str.split("-").str[0]
        d = d[d.series.isin(SERIES)]
        parts.append(d)
        print(f"trades_{p}", len(d), flush=True)
    d = pd.concat(parts, ignore_index=True)
    d["event"] = d.ticker.str.rsplit("-", n=1).str[0]
    d["y"] = d.yes_price.round(2)
    d["tyes"] = d.taker_side.eq("yes")
    d = d.rename(columns={"timestamp_unix": "ts", "count": "qty"})[["ts", "series", "event", "ticker", "y", "tyes", "qty"]]
    d = d.sort_values(["ticker", "ts"], kind="mergesort").reset_index(drop=True)
    d.to_parquet(OUT / "tape.parquet", index=False)
    print("tape rows", len(d))


def parse_markets(df):
    """df with ticker, event_ticker. Adds kind, lo, hi (inclusive integer YES range; +-inf for tails)."""
    df = df.reset_index(drop=True).copy()
    suf = df.ticker.str.rsplit("-", n=1).str[1]
    df["kind"] = suf.str[0]
    df["k"] = pd.to_numeric(suf.str[1:], errors="coerce")
    df = df[df.kind.isin(["B", "T"]) & df.k.notna()].copy()
    # T direction: above the event's bracket centre -> 'greater', below -> 'less' (equivalent to the
    # max-T/min-T rule when both tails are present; also works when settled_markets lacks one tail)
    bmid = df[df.kind == "B"].groupby("event_ticker").k.mean()
    mid = df.event_ticker.map(bmid)
    df["lo"] = np.nan; df["hi"] = np.nan
    b = df.kind == "B"
    df.loc[b, "lo"] = np.floor(df.loc[b, "k"]); df.loc[b, "hi"] = np.floor(df.loc[b, "k"]) + 1
    t = df.kind == "T"
    g = t & (df.k > mid)
    l_ = t & (df.k < mid)
    df.loc[g, "lo"] = df.loc[g, "k"] + 1; df.loc[g, "hi"] = np.inf
    df.loc[l_, "lo"] = -np.inf; df.loc[l_, "hi"] = df.loc[l_, "k"] - 1
    return df


def markets():
    m = pd.read_parquet(ROOT / "data" / "vendor" / "kalshi_research" / "settled_markets.parquet")
    m["series"] = m.event_ticker.str.split("-").str[0]
    m = m[m.series.isin(SERIES)].copy()
    m = parse_markets(m)
    def _ts(x):
        return (pd.to_datetime(x, format="ISO8601", errors="coerce", utc=True)
                - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s")
    m["close_ts"] = _ts(m.close_time); m["settle_ts"] = _ts(m.settlement_ts); m["open_ts"] = _ts(m.open_time)
    m["win"] = m.result.eq("yes").astype(int)
    m["volume"] = m.volume_fp.astype(float)
    # climate date from the event ticker, e.g. KXHIGHNY-25AUG01
    ds = m.event_ticker.str.split("-").str[1]
    m["date"] = pd.to_datetime(ds, format="%y%b%d", errors="coerce").dt.date.astype(str)
    m = m[["series", "event_ticker", "ticker", "date", "kind", "k", "lo", "hi", "win", "volume", "open_ts",
           "close_ts", "settle_ts"]]
    m.to_parquet(OUT / "markets.parquet", index=False)
    print(m.groupby("series").size())
    # integrity: exactly one winner per event and lo/hi present
    w = m.groupby("event_ticker").win.sum()
    print("events", len(w), "with exactly 1 winner", (w == 1).mean(), "missing lo", m.lo.isna().sum())


def check():
    """Validate the ticker->strike convention on fin_E's /markets rows (floor/cap/strike_type)."""
    f = pd.read_parquet(ROOT / "data" / "vendor" / "fin_E" / "kalshi_weather" / "markets.parquet")
    f = f.rename(columns={"event": "event_ticker"})
    p = parse_markets(f)
    ok_b = p[(p.kind == "B")]
    ok_b = ((ok_b["floor"] == ok_b.lo) & (ok_b["cap"] == ok_b.hi)).mean()
    g = p[p.strike_type == "greater"]; l_ = p[p.strike_type == "less"]
    print("B floor/cap match", ok_b, "greater->lo=floor+1", ((g.lo == g["floor"] + 1) & np.isinf(g.hi)).mean(),
          "less->hi=cap-1", ((l_.hi == l_["cap"] - 1) & np.isinf(l_.lo)).mean())
    v = pd.to_numeric(p.expiration_value.astype(str).str.extract(r"(-?\d+(?:\.\d+)?)")[0], errors="coerce")
    print("expiration_value non-numeric examples:", p.expiration_value[v.isna()].unique()[:5])
    print("expiration_value range-like (e.g. '58 to 59'):", p.expiration_value.astype(str).str.contains("to").mean())
    pred = ((v >= p.lo) & (v <= p.hi)).astype(int)
    print("result consistent with expiration_value:", (pred == p.result.eq("yes").astype(int)).mean(), len(p))


if __name__ == "__main__":
    {"extract": extract, "markets": markets, "check": check}[sys.argv[1]]()
