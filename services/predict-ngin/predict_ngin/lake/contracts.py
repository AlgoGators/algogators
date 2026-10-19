"""Data contracts: the schema and invariants each lake dataset must satisfy before it is published.

A contract lists required columns (with a DuckDB type prefix) and checks. Each check is a SQL
expression that is TRUE for a *bad* row; the contract passes when every check counts zero bad rows.
Builders run the contract on each new partition before swapping it in, and ``predict-ngin data
verify`` runs it over a whole dataset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from ..markets.polymarket_category import CATEGORIES


@dataclass(frozen=True)
class Check:
    name: str
    bad_when: str  # SQL predicate over one row; TRUE = violation


@dataclass(frozen=True)
class Contract:
    dataset: str
    columns: Dict[
        str, str
    ]  # column -> DuckDB type prefix ("VARCHAR", "DOUBLE", "BIGINT", "BOOLEAN", "FLOAT")
    checks: List[Check] = field(default_factory=list)
    unique: List[str] = field(
        default_factory=list
    )  # columns that together must not repeat (within one partition)

    def run(self, con, source_sql: str) -> List[dict]:
        """Results for ``SELECT ... FROM (source_sql)``: one dict per check, ``ok`` False on any
        violation."""
        out = []
        have = {r[0]: r[1] for r in con.sql(f"DESCRIBE SELECT * FROM ({source_sql})").fetchall()}
        for col, typ in self.columns.items():
            got = have.get(col)
            out.append(
                {
                    "check": f"column {col} {typ}",
                    "violations": 0 if got and got.startswith(typ) else 1,
                    "detail": got or "missing",
                }
            )
        if any(r["violations"] for r in out):
            return [{**r, "ok": r["violations"] == 0} for r in out]
        if self.checks:
            exprs = ", ".join(f"count(*) FILTER (WHERE {c.bad_when})" for c in self.checks)
            counts = con.sql(f"SELECT {exprs} FROM ({source_sql})").fetchone()
            out += [
                {"check": c.name, "violations": int(n)}
                for c, n in zip(self.checks, counts, strict=False)
            ]
        if self.unique:
            key = ", ".join(self.unique)
            dup = con.sql(
                f"SELECT count(*) - count(DISTINCT ({key})) FROM ({source_sql})"
            ).fetchone()[0]
            out.append({"check": f"unique ({key})", "violations": int(dup)})
        return [{**r, "ok": r["violations"] == 0} for r in out]


def failed(results: List[dict]) -> List[dict]:
    return [r for r in results if not r["ok"]]


_CATS = ", ".join(f"'{c}'" for c in CATEGORIES)

POLYMARKET_FILLS = Contract(
    dataset="polymarket/fills",
    columns={
        "ts_chain": "DOUBLE",
        "market_id": "VARCHAR",
        "yes_price": "DOUBLE",
        "usd_amount": "DOUBLE",
        "is_taker_order_row": "BOOLEAN",
        "transaction_hash": "VARCHAR",
        "category": "VARCHAR",
        "is_updown": "BOOLEAN",
    },
    checks=[
        Check("ts_chain present", "ts_chain IS NULL"),
        Check("market_id present", "market_id IS NULL"),
        # taker-order aggregate rows carry usd/tokens over several fills and can leave [0, 1]
        # (3 rows in the whole tape); they are excluded from counts and volume anyway, so the
        # check applies to fills only
        Check(
            "maker-fill yes_price in [0, 1]",
            "NOT is_taker_order_row AND (yes_price < 0 OR yes_price > 1)",
        ),
        Check("usd_amount >= 0", "usd_amount < 0"),
        Check(
            "transaction_hash is 0x-prefixed lowercase",
            "transaction_hash NOT LIKE '0x%' OR transaction_hash <> lower(transaction_hash)",
        ),
        Check("category known", f"category NOT IN ({_CATS})"),
    ],
)

POLYMARKET_MARKETS = Contract(
    dataset="polymarket/markets",
    columns={
        "market_id": "VARCHAR",
        "event_id": "VARCHAR",
        "category": "VARCHAR",
        "is_updown": "BOOLEAN",
    },
    checks=[
        Check("market_id present", "market_id IS NULL"),
        Check("category known", f"category NOT IN ({_CATS})"),
    ],
    unique=["market_id"],
)

KALSHI_TRADES = Contract(
    dataset="kalshi/trades",
    columns={
        "trade_id": "VARCHAR",
        "ticker": "VARCHAR",
        "event_ticker": "VARCHAR",
        "yes_price": "DOUBLE",
        "no_price": "DOUBLE",
        "count": "BIGINT",
        "count_fp": "DOUBLE",
        "taker_side": "VARCHAR",
        "timestamp_unix": "BIGINT",
    },
    checks=[
        Check("trade_id present", "trade_id IS NULL"),
        Check("ticker present", "ticker IS NULL"),
        Check("event_ticker present", "event_ticker IS NULL"),
        Check("prices in [0, 1]", "yes_price < 0 OR yes_price > 1 OR no_price < 0 OR no_price > 1"),
        # sub-cent prices are floored on both legs, so yes + no is 1.00 or 0.99
        Check("yes + no within a cent of 1", "abs(yes_price + no_price - 1) > 0.0101"),
        Check("count_fp > 0", "count_fp IS NULL OR count_fp <= 0"),
        Check("taker_side yes/no", "taker_side NOT IN ('yes', 'no')"),
    ],
    unique=["trade_id"],
)

ALL = {c.dataset: c for c in (POLYMARKET_FILLS, POLYMARKET_MARKETS, KALSHI_TRADES)}
