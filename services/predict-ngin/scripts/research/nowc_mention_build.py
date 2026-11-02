"""nowc: earnings-call mention markets + point-in-time base-rate features.

Features (all strictly causal relative to the event's call):
  kb_n, kb_said, kb_last : Kalshi's own prior settled markets for the SAME company and SAME normalised word
                           (earlier events whose close < this event's first open; i.e. prior calls).
  pr_n, pr_hits, pr_last : SEC EDGAR earnings press releases (8-K 2.02 / 6-K EX-99.x) accepted before
                           (event close - 48h): number of the last <=4 releases and how many contain the word
                           (Kalshi rule: exact word or plural/possessive; compound/hyphenated words excluded).
  pr_same                : word appears in a release accepted in [close-48h, close) (the same-quarter release;
                           causal only from its acceptance time pr_same_ts).
Writes data/vendor/nowc/mention_markets.parquet and mention_trades.parquet.
"""
from __future__ import annotations
import glob, re
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FD = ROOT / "data/vendor/fin_D"; OUT = ROOT / "data/vendor/nowc"; ED = OUT / "edgar"
TAPE_END = pd.Timestamp("2026-02-11", tz="UTC").value // 10**9
E = pd.Timestamp(0, tz="UTC")
ALIAS = {"ADOBE": "ADBE", "ALIBABA": "BABA", "COINBASE": "COIN", "DOLLARGENERAL": "DG", "LUCID": "LCID", "TSMC": "TSM"}


def ts(s):
    return (pd.to_datetime(s, utc=True, format="ISO8601") - E) // pd.Timedelta(seconds=1)


def company(series):
    t = re.sub(r"^(KXEARNINGSMENTION|KXMENTIONEARN)", "", series)
    return ALIAS.get(t, t)


def variants(word):
    return [v.strip() for v in re.split(r"\s*/\s*", word) if v.strip()]


def word_rx(word):
    parts = []
    for v in variants(word):
        toks = [re.escape(x) for x in re.split(r"\s+", v)]
        core = r"\s+".join(toks)
        # not preceded/followed by a letter, digit or hyphen (compound / hyphenated words excluded)
        parts.append(r"(?<![\w\-])" + core + r"(?:s|es|'s|’s|s'|s’)?(?![\w\-])")
    return re.compile("|".join(parts), re.I)


def main():
    m = pd.read_parquet(FD / "api_earnmention_markets.parquet")
    m = m[m.status.isin(["finalized", "settled"]) & m.result.isin(["yes", "no"])].copy()
    m["series"] = m.event_ticker.str.split("-").str[0]
    m["company"] = m.series.map(company)
    m["word"] = m.yes_sub_title.fillna(m.custom_strike.astype(str)).str.strip()
    m["wkey"] = m.word.str.lower().str.replace(r"\s+", " ", regex=True)
    m["close_ts"] = ts(m.close_time); m["open_ts"] = ts(m.open_time); m["settle_ts"] = ts(m.settlement_ts)
    ev = m.groupby("event_ticker").agg(ev_close=("close_ts", "max"), ev_open=("open_ts", "min"), company=("company", "first"))
    m = m.merge(ev[["ev_close", "ev_open"]], left_on="event_ticker", right_index=True)
    m["y"] = (m.result == "yes").astype(int)
    # Kalshi prior-call base rate
    kb = []
    for r in m.itertuples():
        p = m[(m.company == r.company) & (m.wkey == r.wkey) & (m.ev_close < r.ev_open)]
        p = p.sort_values("ev_close")
        kb.append((len(p), int(p.y.sum()), int(p.y.iloc[-1]) if len(p) else -1))
    m[["kb_n", "kb_said", "kb_last"]] = kb
    # EDGAR press releases
    fl = pd.read_parquet(ED / "filings.parquet")
    fl["acc_ts"] = ts(fl.accepted)
    texts = {a: (ED / "text" / f"{a}.txt").read_text(encoding="utf-8") for a in fl.accession}
    fl = fl[fl.nchar > 1500]
    pr = []
    for r in m.itertuples():
        f = fl[fl.ticker == r.company].sort_values("acc_ts")
        if f.empty:
            pr.append((np.nan, np.nan, np.nan, np.nan, np.nan)); continue
        rx = word_rx(r.word)
        prior = f[f.acc_ts < r.ev_close - 48 * 3600].tail(4)
        hits = [bool(rx.search(texts[a])) for a in prior.accession]
        same = f[(f.acc_ts >= r.ev_close - 48 * 3600) & (f.acc_ts < r.ev_close)]
        ps = int(any(rx.search(texts[a]) for a in same.accession)) if len(same) else -1
        pr.append((len(prior), sum(hits), int(hits[-1]) if hits else -1, ps,
                   same.acc_ts.min() if len(same) else np.nan))
    m[["pr_n", "pr_hits", "pr_last", "pr_same", "pr_same_ts"]] = pr
    keep = ["ticker", "event_ticker", "series", "company", "word", "wkey", "result", "y", "close_ts", "open_ts",
            "settle_ts", "ev_close", "ev_open", "volume_fp", "kb_n", "kb_said", "kb_last", "pr_n", "pr_hits",
            "pr_last", "pr_same", "pr_same_ts"]
    m[keep].to_parquet(OUT / "mention_markets.parquet")
    # trades: tape (pre TAPE_END closes) + API
    tp = pd.read_parquet(FD / "mention_trades.parquet", filters=[("earn", "==", True)])
    tp = tp[tp.close_ts <= TAPE_END]
    t1 = pd.DataFrame({"ticker": tp.ticker, "tus": tp.ts.astype(np.int64) * 10**6, "qty": tp["count"].astype(float),
                       "y": tp.yes_price.astype(float), "tyes": tp.taker_side == "yes"})
    tr = []
    for f in glob.glob(str(FD / "api_earnmention_trades" / "*.parquet")):
        x = pd.read_parquet(f)
        if "created_time" in x.columns and x.trade_id.notna().any():
            tr.append(x[["ticker", "created_time", "count_fp", "yes_price_dollars", "taker_side"]])
    tr = pd.concat(tr)
    t2 = pd.DataFrame({"ticker": tr.ticker.values,
                       "tus": ((pd.to_datetime(tr.created_time, utc=True, format="ISO8601") - E) // pd.Timedelta(microseconds=1)).values,
                       "qty": tr.count_fp.astype(float).values, "y": tr.yes_price_dollars.astype(float).values,
                       "tyes": (tr.taker_side == "yes").values})
    t2 = t2[~t2.ticker.isin(set(t1.ticker))]
    t = pd.concat([t1, t2], ignore_index=True)
    t = t.merge(m[["ticker", "event_ticker", "close_ts", "settle_ts", "ev_close"]], on="ticker")
    t = t[(t.qty > 0) & t.y.between(0.01, 0.99)]
    t["ts"] = t.tus // 10**6
    t = t[t.ts < t.close_ts].sort_values(["ticker", "tus"], kind="mergesort").reset_index(drop=True)
    t.to_parquet(OUT / "mention_trades.parquet")
    print(len(m), "markets", m.event_ticker.nunique(), "events;", len(t), "trades")
    print("kb coverage", (m.kb_n > 0).mean(), "pr coverage", (m.pr_n > 0).mean(), "pr_same avail", (m.pr_same >= 0).mean())


if __name__ == "__main__":
    main()
