"""whale_pit stage 2: point-in-time wallet skill scores and follower signals.

Input: ``data/research/whale_pit/trades/*.parquet`` and ``markets.parquet`` (stage 1).
Output: ``data/research/whale_pit/signals.parquet``: one row per taker trade of at least $1,000
(2024-01-01 .. 2025-12-31) with the wallet's skill statistics as of the trade time, causal prices
for a follower and the outcome fields needed to score the follower afterwards.

Skill events (one per scored taker trade of >= $100 by the same wallet):
  * ``mk``  - 24h markout per $ at the last hourly bar close available at ts + 24h. Known at ts + 24h.
  * ``res`` - resolution return per $ (token payoff vs vwap). Known at the market's ``known_ts``
             (max(last fill, scheduled end) + 1 day; never for markets still trading in Dec 2025).
Scores at a signal use only events with known time strictly before the signal's block time
(DuckDB ASOF JOIN ... AND s.ts > c.k on per-wallet cumulative sums).

    uv run --no-sync python scripts/research/whale_pit_signals.py
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from predict_ngin.shared.duck import connect, sql_path
from predict_ngin.shared.paths import data_root

# Data end (exclusive). Default 2026-01-01 = the original study; WHALE_PIT_END=2026-10-17 runs the
# 2026 extension into its own folder (data/research/whale_pit/end_2026-10-17), leaving the original intact.
END = os.environ.get("WHALE_PIT_END", "2026-01-01")
DATA_END_TS = int(datetime.fromisoformat(END).replace(tzinfo=timezone.utc).timestamp())
_ORIG = END == "2026-01-01"
OUT = data_root() / "research" / "whale_pit" / ("" if _ORIG else f"end_{END}")
LAKE = data_root() / "lake" / "polymarket"
H = 86400                      # markout / short-exit horizon (s)
SIG_MIN_USD = 1000.0
SIG_START, SIG_END = 1704067200, DATA_END_TS  # 2024-01-01 .. data end


def main() -> int:
    t0 = time.time()
    con = connect(memory="2GB", threads=2)
    trades = sql_path(OUT / "trades" / "*.parquet")
    mk = sql_path(OUT / "markets.parquet")
    bars = sql_path(LAKE / "bars_1h" / "**" / "*.parquet")
    tmp = OUT / "_tmp"
    tmp.mkdir(exist_ok=True)

    def step(msg):
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    def chunked(name: str, key: str, k: int, sql: str) -> str:
        """Run ``sql`` (with {c}/{k} placeholders filtering hash(key) % k = c) chunk by chunk to Parquet;
        return a read_parquet expression over the parts. Keeps ASOF joins and windows small."""
        parts = []
        for c in range(k):
            part = tmp / f"{name}.{c}.parquet"
            con.execute(f"COPY ({sql.format(c=c, k=k)}) TO {sql_path(part)} (FORMAT parquet)")
            parts.append(sql_path(part))
        return f"read_parquet([{', '.join(parts)}])"

    # Hourly bars of eligible markets; a bar is usable once its hour has ended.
    b = chunked("bars", "market_id", 1, f"""
        SELECT market_id, ts + 3600 AS avail, close,
               sum(usd) OVER (PARTITION BY market_id ORDER BY ts) AS cum_usd
        FROM read_parquet({bars}, hive_partitioning=true)
        WHERE ts < {DATA_END_TS} AND market_id IN (SELECT market_id FROM read_parquet({mk}))""")
    step("bars")

    # Wallets that ever place a signal-sized trade (pure compute pruning: a wallet's score matters
    # only at its own signal trades, so this uses no information a live follower would lack).
    t = chunked("t", "w", 1, f"""
        SELECT tr.w, tr.market_id, tr.d, tr.ts, tr.usd, tr.p_vwap, tr.p_max, tr.p_min,
               tr.p_ref60, tr.d_ref60, tr.p_ref300, tr.d_ref300,
               m.event_id, m.category, m.yes_payoff, m.known_ts, m.last_fill_ts
        FROM read_parquet({trades}) tr JOIN read_parquet({mk}) m USING (market_id)
        WHERE tr.w IN (SELECT DISTINCT w FROM read_parquet({trades}) WHERE usd >= {SIG_MIN_USD})""")
    step(f"trades of signal wallets {con.sql(f'SELECT count(*) FROM {t}').fetchone()[0]:,}")

    # Skill events: 24h markout (known at ts + 24h) and resolution return (known at known_ts).
    ev = chunked("ev", "market_id", 8, f"""
        WITH tt AS (SELECT * FROM {t} WHERE hash(market_id) % {{k}} = {{c}}),
             bb AS (SELECT * FROM {b} WHERE hash(market_id) % {{k}} = {{c}}),
             e AS (SELECT tt.w, tt.ts, tt.d, tt.usd, tt.p_vwap, tt.yes_payoff, tt.known_ts, bb.close AS p_h
                   FROM tt ASOF LEFT JOIN bb ON tt.market_id = bb.market_id AND tt.ts + {H} >= bb.avail)
        SELECT w, ts + {H} AS k, 'mk' AS kind, usd,
               usd * d * (p_h - p_vwap) / CASE WHEN d = 1 THEN p_vwap ELSE 1 - p_vwap END AS pnl
        FROM e WHERE p_h IS NOT NULL
        UNION ALL
        SELECT w, known_ts AS k, 'res' AS kind, usd,
               usd * ((CASE WHEN d = 1 THEN yes_payoff ELSE 1 - yes_payoff END)
                      - CASE WHEN d = 1 THEN p_vwap ELSE 1 - p_vwap END)
                   / CASE WHEN d = 1 THEN p_vwap ELSE 1 - p_vwap END AS pnl
        FROM e WHERE yes_payoff IS NOT NULL AND known_ts < 1e18""")
    con.execute(f"COPY (SELECT * FROM {ev}) TO {sql_path(OUT / 'events.parquet')} (FORMAT parquet)")
    step(f"skill events {con.sql(f'SELECT count(*) FROM {ev}').fetchone()[0]:,}")

    # Signals with as-of skill: per-wallet cumulative sums ordered by known time, ASOF on ts > k.
    s2 = chunked("s2", "w", 8, f"""
        WITH s0 AS (SELECT * FROM {t} WHERE usd >= {SIG_MIN_USD} AND ts >= {SIG_START} AND ts < {SIG_END}
                      AND hash(w) % {{k}} = {{c}}),
             e AS (SELECT * FROM {ev} WHERE hash(w) % {{k}} = {{c}}),
             c_mk AS (SELECT w, k, sum(usd) OVER win AS cu, sum(pnl) OVER win AS cp, count(*) OVER win AS cn
                      FROM e WHERE kind = 'mk' WINDOW win AS (PARTITION BY w ORDER BY k)),
             c_res AS (SELECT w, k, sum(usd) OVER win AS cu, sum(pnl) OVER win AS cp, count(*) OVER win AS cn
                       FROM e WHERE kind = 'res' WINDOW win AS (PARTITION BY w ORDER BY k)),
             s1 AS (SELECT s0.*, c.cu AS mk_u, c.cp AS mk_p, c.cn AS mk_n, c.k AS mk_k
                    FROM s0 ASOF LEFT JOIN c_mk c ON s0.w = c.w AND s0.ts > c.k)
        SELECT s1.*, c.cu AS res_u, c.cp AS res_p, c.cn AS res_n, c.k AS res_k
        FROM s1 ASOF LEFT JOIN c_res c ON s1.w = c.w AND s1.ts > c.k""")
    step(f"signals {con.sql(f'SELECT count(*) FROM {s2}').fetchone()[0]:,}")

    # Causal market context: trailing-24h volume, price 24h before, price at entry + 24h; last price.
    chunked("signals", "market_id", 4, f"""
        WITH s AS (SELECT * FROM {s2} WHERE hash(market_id) % {{k}} = {{c}}),
             bb AS (SELECT * FROM {b} WHERE hash(market_id) % {{k}} = {{c}}),
             a AS (SELECT s.*, bb.cum_usd AS cum_now, bb.close AS p_now_bar
                   FROM s ASOF LEFT JOIN bb ON s.market_id = bb.market_id AND s.ts >= bb.avail),
             a2 AS (SELECT a.*, bb.cum_usd AS cum_24h_ago, bb.close AS p_24h_ago
                    FROM a ASOF LEFT JOIN bb ON a.market_id = bb.market_id AND a.ts - {H} >= bb.avail),
             a3 AS (SELECT a2.*, bb.close AS p_exit60
                    FROM a2 ASOF LEFT JOIN bb ON a2.market_id = bb.market_id AND a2.ts + 60 + {H} >= bb.avail),
             a4 AS (SELECT a3.*, bb.close AS p_exit300
                    FROM a3 ASOF LEFT JOIN bb ON a3.market_id = bb.market_id AND a3.ts + 300 + {H} >= bb.avail)
        SELECT a4.*, bb.close AS p_last
        FROM a4 ASOF LEFT JOIN bb ON a4.market_id = bb.market_id AND a4.last_fill_ts >= bb.avail""")
    con.execute(f"COPY (SELECT * FROM read_parquet({sql_path(tmp / 'signals.*.parquet')})) "
                f"TO {sql_path(OUT / 'signals.parquet')} (FORMAT parquet)")
    for f in tmp.glob("*.parquet"):
        f.unlink()
    step("wrote signals.parquet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
