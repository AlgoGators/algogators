"""Verify (do not assume) that outcome index 0 / clobTokenIds[0] / token1 is the YES-equivalent token.

For 50 random resolved markets (seed 20261015; 25 plain Yes/No, 15 neg-risk Yes/No, 10 named-outcome markets)
that traded on the CLOB in data/polymarket/trades.parquet:

1. CLOB API ``/markets/{condition_id}`` (independent source, public read-only): ``tokens[0].token_id`` must equal
   markets.parquet ``token1`` and ``tokens[0].outcome`` must equal ``answer1``; ``winner`` flags give the result.
2. The resolved winner index from metadata ``outcome_prices`` must match the CLOB ``winner`` flag.
3. On-chain: the last prints of the token1 asset (from the raw fills) must trade near 1 iff token1 won,
   and ``nonusdc_side == 'token1'`` must hold exactly for the asset_id that equals token1.

Writes data/vendor/pmfix/verify_yes_index.csv and prints a summary.
    venv/Scripts/python.exe scripts/research/pmfix_verify_yes_index.py
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "pmfix" / "verify_yes_index.csv"
TRADES = (ROOT / "data" / "polymarket" / "trades.parquet").as_posix()
MARKETS = (ROOT / "data" / "polymarket" / "markets.parquet").as_posix()
EXCH = ("0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e", "0xc5d563a36ae78145c45a50134d48a1215220f80a")


def winner_idx(op) -> int | None:
    try:
        v = [float(x) for x in ast.literal_eval(op)]
    except Exception:  # noqa: BLE001
        return None
    if len(v) == 2 and sorted(v) == [0.0, 1.0]:
        return int(np.argmax(v))
    return None


def main() -> None:
    c = duckdb.connect()
    c.execute("SET enable_progress_bar=false")
    m = c.sql(f"select id, condition_id, token1, token2, answer1, answer2, neg_risk, outcome_prices, question "
              f"from '{MARKETS}' where closed").df()
    m["w"] = m.outcome_prices.map(winner_idx)
    m = m[m.w.notna()]
    # markets that actually have CLOB prints (count per market from the tape, one column scan)
    cnt = c.sql(f"select market_id, count(*) n from read_parquet('{TRADES}') group by 1").df()
    m = m.merge(cnt, left_on="id", right_on="market_id")
    m = m[m.n >= 30]
    rng = np.random.default_rng(20261015)
    yn = (m.answer1 == "Yes") & (m.answer2 == "No")
    pools = [(m[yn & ~m.neg_risk], 25), (m[yn & m.neg_risk], 15), (m[~yn], 10)]
    s = pd.concat([p.iloc[rng.choice(len(p), k, replace=False)] for p, k in pools], ignore_index=True)
    print(f"sampled {len(s)} of {len(m)} eligible resolved markets", flush=True)

    rows = []
    for r in s.itertuples():
        j = None
        for attempt in range(4):
            try:
                j = requests.get(f"https://clob.polymarket.com/markets/{r.condition_id}", timeout=30).json()
                break
            except Exception:  # noqa: BLE001
                time.sleep(2 + 2 * attempt)
        time.sleep(1.0)
        toks = (j or {}).get("tokens") or []
        rows.append({
            "market_id": r.id, "condition_id": r.condition_id, "question": r.question[:80], "neg_risk": r.neg_risk,
            "answer1": r.answer1, "answer2": r.answer2, "token1": r.token1, "token2": r.token2,
            "meta_winner_idx": int(r.w), "n_prints": int(r.n),
            "clob_tok0": toks[0]["token_id"] if len(toks) > 0 else None,
            "clob_out0": toks[0]["outcome"] if len(toks) > 0 else None,
            "clob_tok1": toks[1]["token_id"] if len(toks) > 1 else None,
            "clob_winner_idx": next((i for i, t in enumerate(toks) if t.get("winner")), None),
        })
    v = pd.DataFrame(rows)
    ids = ",".join(f"'{x}'" for x in v.market_id)
    tape = c.sql(f"select market_id, asset_id, nonusdc_side, block_number, price, lower(taker) taker "
                 f"from read_parquet('{TRADES}') where market_id in ({ids})").df()
    tape = tape[~tape.taker.isin(EXCH)]
    out = []
    for r in v.itertuples():
        t = tape[tape.market_id == r.market_id].sort_values("block_number")
        t1 = t[t.asset_id == r.token1]
        t2 = t[t.asset_id == r.token2]
        last1 = float(t1.price.tail(20).median()) if len(t1) else np.nan
        last2 = float(t2.price.tail(20).median()) if len(t2) else np.nan
        # implied final price of token1 from whichever token printed last
        if len(t1) and (not len(t2) or t1.block_number.iloc[-1] >= t2.block_number.iloc[-1]):
            final1 = last1
        else:
            final1 = 1 - last2
        out.append({
            "market_id": r.market_id,
            "ns_token1_matches_asset": bool(((t.nonusdc_side == "token1") == (t.asset_id == r.token1)).all()),
            "n_tok1": len(t1), "n_tok2": len(t2), "last_tok1_px": last1, "last_tok2_px": last2,
            "final_tok1_px": final1,
        })
    v = v.merge(pd.DataFrame(out), on="market_id")
    v["clob_order_ok"] = (v.clob_tok0 == v.token1) & (v.clob_tok1 == v.token2)
    v["clob_label_ok"] = v.clob_out0.astype(str).str.lower() == v.answer1.astype(str).str.lower()
    v["winner_meta_vs_clob_ok"] = v.meta_winner_idx == v.clob_winner_idx
    v["onchain_final_ok"] = np.where(v.final_tok1_px > 0.5, 0, 1) == v.clob_winner_idx.fillna(v.meta_winner_idx)
    v.to_csv(OUT, index=False)
    print(v[["market_id", "answer1", "neg_risk", "meta_winner_idx", "clob_winner_idx", "final_tok1_px",
             "clob_order_ok", "clob_label_ok", "ns_token1_matches_asset", "onchain_final_ok"]].to_string())
    for col in ("clob_order_ok", "clob_label_ok", "winner_meta_vs_clob_ok", "ns_token1_matches_asset", "onchain_final_ok"):
        print(f"{col}: {int(v[col].sum())}/{len(v)}")


if __name__ == "__main__":
    main()
