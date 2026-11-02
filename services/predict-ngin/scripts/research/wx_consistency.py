"""wx (d): consistency. (1) intra-event: sum of YES asks < $1 (buy every bracket) or sum of YES bids > $1
(buy every NO) after taker fees, per minute on fin_E 1-min candles (18 high series, 2026-08-15..10-12).
(2) cross-city: market-implied mean high for station pairs vs the forecast-implied difference.
"""
from __future__ import annotations
import glob, sys, warnings
warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, ROOT, SERIES  # noqa: E402

fee = lambda b: np.ceil(np.round(0.07 * b * (1 - b) * 100, 6)) / 100
mk = pd.read_parquet(ROOT / "data/vendor/fin_E/kalshi_weather/markets.parquet")
nmk = mk.groupby("event").ticker.nunique()
rows = []; implied = []
for f in glob.glob(str(ROOT / "data/vendor/fin_E/kalshi_weather/candles/*/*.parquet")):
    c = pd.read_parquet(f)
    for col in ("bid_close", "ask_close"):
        c[col] = pd.to_numeric(c[col], errors="coerce")
    ev = Path(f).stem
    c = c[(c.ask_close > 0) & (c.ask_close < 1)]
    w = c.pivot_table(index="ts", columns="ticker", values=["ask_close", "bid_close"])
    if w.empty or w["ask_close"].shape[1] < nmk.get(ev, 99):
        continue
    w = w.dropna()
    a = w["ask_close"]; b = w["bid_close"]
    buy_all = 1 - a.sum(1) - fee(a).sum(1)
    sell_all = b.sum(1) - 1 - fee(1 - b).sum(1)
    rows.append(pd.DataFrame({"event": ev, "ts": w.index, "buy_all": buy_all.values, "sell_all": sell_all.values}))
    m = mk[mk.event == ev].set_index("ticker")
    ctr = np.where(m.strike_type == "between", (m["floor"] + m["cap"]) / 2,
                   np.where(m.strike_type == "greater", m["floor"] + 2, m["cap"] - 2))
    mid = (a + b) / 2
    mid = mid.div(mid.sum(1), axis=0)
    implied.append(pd.DataFrame({"event": ev, "ts": w.index, "imean": (mid * pd.Series(ctr, index=m.index).reindex(mid.columns).values).sum(1).values}))
r = pd.concat(rows)
print("event-minutes", len(r), "events", r.event.nunique())
for col in ("buy_all", "sell_all"):
    x = r[r[col] > 0]
    print(f"{col}: minutes with locked profit >0 after fees: {len(x)} ({len(x)/len(r):.4%}), events {x.event.nunique()}, "
          f"median profit {x[col].median() if len(x) else 0:.3f}")
im = pd.concat(implied)
im["series"] = im.event.str.split("-").str[0]; im["date"] = im.event.str.split("-").str[1]
im.to_parquet(OUT / "implied_mean.parquet", index=False)
# cross-city: pairs of nearby stations; spread of implied-mean difference vs realised CLI difference
pairs = [("KXHIGHTSATX", "KXHIGHAUS"), ("KXHIGHTHOU", "KXHIGHAUS"), ("KXHIGHTDAL", "KXHIGHTOKC"), ("KXHIGHPHIL", "KXHIGHTDC")]
cli = pd.read_parquet(OUT / "cli.parquet")
mk2 = mk.drop_duplicates("event").assign(series=lambda d: d.series)
for s1, s2 in pairs:
    a1 = im[im.series == s1].copy(); a2 = im[im.series == s2].copy()
    j = a1.merge(a2, on=["date", "ts"], suffixes=("1", "2"))
    if j.empty:
        continue
    j["d"] = j.imean1 - j.imean2
    last = j.sort_values("ts").groupby("date").tail(60)  # last hour before the last common candle
    def _ev(sr):
        z = mk2[mk2.series == sr]
        return pd.Series(z.expiration_value.astype(float).values, index=z.event.str.split("-").str[1].values)
    ev1 = _ev(s1); ev2 = _ev(s2)
    real = (ev1 - ev2).rename("real")
    g = j.groupby("date").d.agg(["first", "last"]).join(real, how="inner")
    print(s1, s2, "days", len(g), "corr(first implied diff, realised diff)", round(g["first"].corr(g.real), 3),
          "MAE first", round((g["first"] - g.real).abs().mean(), 2), "MAE last", round((g["last"] - g.real).abs().mean(), 2))
