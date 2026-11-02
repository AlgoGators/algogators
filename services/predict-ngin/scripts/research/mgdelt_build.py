"""mgdelt step 1: point-in-time panel of speaker mention markets (no GDELT data yet).

Universe and decision time per docs/MGDELT_NEWS_ATTENTION.md (pre-registration section):
- 63 single-speaker series (data/vendor/release/wiki/speaker_map.csv, fixed in the wiki study).
- D = UTC day of (ev_end - 24h); decision time td = D 00:15 UTC (GDELT daily bins through D-1
  are complete at 00:00 and indexed within 15 min).
- market eligible iff close_ts > td, >= 1 print in [td - 72h, td], result yes/no.
- price = YES price of the last print <= td, clipped to [0.01, 0.99]. No volume filter.
- split: train ev_end < 2026-02-01 UTC, test otherwise.
- compliant flag: excludes Executive-Office speakers (President, VP, Press Secretary, First Lady),
  following the Aug 2026 CFTC action over presidential mention markets.
Output: data/vendor/mgdelt/panel.parquet
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from mgdelt_common import OUT, SPEAKER_Q, clean_terms, term_query  # noqa: E402

SPLIT = int(pd.Timestamp("2026-02-01", tz="UTC").timestamp())
WH = {"Donald_Trump", "JD_Vance", "Karoline_Leavitt", "Melania_Trump"}

sm = pd.read_csv("data/vendor/release/wiki/speaker_map.csv")
d = pd.read_parquet("data/vendor/fin_D/mention_sim_input.parquet",
                    columns=["ticker", "event_ticker", "series_ticker", "result", "close_ts", "ev_end",
                             "yes_price", "ts"],
                    filters=[("series_ticker", "in", list(sm.series_ticker))])
d["D"] = ((d.ev_end - 86400) // 86400) * 86400
d["td"] = d.D + 900
mk = d.drop_duplicates("ticker")[["ticker", "event_ticker", "series_ticker", "result", "close_ts", "ev_end",
                                  "D", "td"]]
pre = d[(d.ts <= d.td) & (d.ts >= d.td - 72 * 3600)].sort_values(["ticker", "ts"])
last = pre.groupby("ticker").tail(1)[["ticker", "yes_price", "ts"]].rename(columns={"ts": "px_ts",
                                                                                  "yes_price": "p_raw"})
n72 = pre.groupby("ticker").size().rename("n_prints72")
p = mk.merge(last, on="ticker").merge(n72, on="ticker")
p = p[(p.close_ts > p.td) & p.result.isin(["yes", "no"])].copy()
p["y"] = (p.result == "yes").astype(int)
p["p"] = p.p_raw.clip(0.01, 0.99)
p["split"] = np.where(p.ev_end < SPLIT, "train", "test")
p = p.merge(sm.rename(columns={"article": "speaker"}), on="series_ticker")
p["compliant"] = ~p.speaker.isin(WH)

meta = pd.concat([pd.read_parquet(f"data/vendor/fin_D/api_{k}_markets.parquet",
                                  columns=["ticker", "custom_strike", "yes_sub_title", "market_type"])
                  for k in ("othermention", "earnmention")]).drop_duplicates("ticker")


def word(r):
    w = None
    if isinstance(r.custom_strike, str):
        try:
            w = json.loads(r.custom_strike).get("Word")
        except Exception:
            w = None
    return w or r.yes_sub_title


meta["word"] = meta.apply(word, axis=1)
p = p.merge(meta[["ticker", "word", "market_type"]], on="ticker", how="left")
kw = json.load(open("data/vendor/release/wiki/kalshi_words.json"))
kwm = {t: w for ev in kw.values() for t, w in ev.items()}
p["word"] = p.word.fillna(p.ticker.map(kwm))
p = p[(p.market_type.isna()) | (p.market_type == "binary")]
p["alts"] = p.word.map(lambda w: clean_terms(w) if isinstance(w, str) else [])
p["term_q"] = p.alts.map(lambda a: term_query(a) if a else None)
p["spk_q"] = p.speaker.map(SPEAKER_Q)
p["Dstr"] = pd.to_datetime(p.D, unit="s", utc=True).dt.strftime("%Y-%m-%d")
p["alts"] = p.alts.map(lambda a: " / ".join(a))
os.makedirs(OUT, exist_ok=True)
p.to_parquet(OUT + "panel.parquet", index=False)
print(p.groupby(["split", "compliant"]).agg(n=("ticker", "size"), ev=("event_ticker", "nunique"),
                                            yes=("y", "mean"), p=("p", "mean"),
                                            noterm=("term_q", lambda s: s.isna().mean())))
for sp in ("train", "test"):
    q = p[p.split == sp]
    print(sp, "unique (speaker,term) pairs:", q[["spk_q", "term_q"]].drop_duplicates().shape[0],
          "compliant:", q[q.compliant][["spk_q", "term_q"]].drop_duplicates().shape[0])
