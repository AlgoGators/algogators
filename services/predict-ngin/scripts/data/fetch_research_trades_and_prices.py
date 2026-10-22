#!/usr/bin/env python3
"""
Download trades and price history for research markets from Polymarket API.

Reads data/research/{category}/markets_filtered.csv (top 500 per category).
For each market: fetches all trades (Data API, paginated) and price history
(CLOB API, 1h or 1m). Saves to data/research/{category}/trades.parquet and
prices.parquet so user/trader stats are complete.

Usage:
    python scripts/data/fetch_research_trades_and_prices.py
    python scripts/data/fetch_research_trades_and_prices.py --research-dir data/research --top-n 500
    python scripts/data/fetch_research_trades_and_prices.py --prices-only  # skip trades
"""

import argparse
import ast
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from predict_ngin.markets.polymarket_tokens import parse_list_field  # noqa: E402
from predict_ngin.venues.polymarket.endpoints import CLOB_URL as CLOB_API  # noqa: E402
from predict_ngin.venues.polymarket.endpoints import DATA_API_URL as DATA_API  # noqa: E402
TRADE_LIMIT = 10_000   # max per request
TRADE_OFFSET_MAX = 10_000  # API offset limit
REQUEST_DELAY = 0.2


def _parse_clob_token_ids(raw: Any) -> List[str]:
    """clobTokenIds in any on-disk encoding (predict_ngin.markets.polymarket_tokens)."""
    return parse_list_field(raw)


def _sanitize_trade_df(trade_df: pd.DataFrame) -> None:
    """Ensure parquet-safe dtypes: numeric columns as int64/float64, rest as string."""
    if "timestamp" in trade_df.columns:
        trade_df["timestamp"] = pd.to_numeric(trade_df["timestamp"], errors="coerce").fillna(0).astype("int64")
    for col in ("size", "price", "outcomeIndex"):
        if col in trade_df.columns:
            trade_df[col] = pd.to_numeric(trade_df[col], errors="coerce")
    # Force object columns to string so parquet doesn't infer wrong type
    for col in trade_df.columns:
        if trade_df[col].dtype == object:
            trade_df[col] = trade_df[col].astype(str)


def _normalize_trade(row: Dict[str, Any], condition_id: str) -> None:
    """Ensure numeric fields are int/float for parquet."""
    row["conditionId"] = condition_id
    if "timestamp" in row and row["timestamp"] is not None:
        try:
            row["timestamp"] = int(float(row["timestamp"]))
        except (TypeError, ValueError):
            row["timestamp"] = 0
    for k in ("size", "price", "outcomeIndex"):
        if k in row and row[k] is not None:
            try:
                row[k] = float(row[k])
            except (TypeError, ValueError):
                row[k] = None


def fetch_trades_for_market(condition_id: str, session: requests.Session) -> List[Dict[str, Any]]:
    """Fetch all trades for one market (conditionId). Paginates with limit 10000."""
    out: List[Dict[str, Any]] = []
    offset = 0
    while offset <= TRADE_OFFSET_MAX:
        try:
            r = session.get(
                f"{DATA_API}/trades",
                params={"market": condition_id, "limit": TRADE_LIMIT, "offset": offset},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for row in data:
                _normalize_trade(row, condition_id)
            out.extend(data)
            if len(data) < TRADE_LIMIT:
                break
            offset += len(data)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            break
    return out


def fetch_price_history(token_id: str, session: requests.Session, interval: str = "1h") -> List[Dict[str, Any]]:
    """Fetch price history for one CLOB token. interval: 1m, 1h, 1d, etc."""
    try:
        r = session.get(
            f"{CLOB_API}/prices-history",
            params={"market": token_id, "interval": interval},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        history = data.get("history") or []
        return [{"token_id": token_id, "t": h["t"], "p": h["p"]} for h in history]
    except Exception:
        return []


def load_research_markets(research_dir: Path, category: Optional[str] = None) -> Dict[str, pd.DataFrame]:
    """Load markets_filtered.csv per category. Returns {category_dir_name: DataFrame}."""
    out: Dict[str, pd.DataFrame] = {}
    for cat_dir in research_dir.iterdir():
        if not cat_dir.is_dir():
            continue
        if category and cat_dir.name != category:
            continue
        p = cat_dir / "markets_filtered.csv"
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, low_memory=False)
            if df.empty or "conditionId" not in df.columns:
                continue
            df["conditionId"] = df["conditionId"].astype(str).str.strip()
            out[cat_dir.name] = df
        except Exception:
            continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch trades and prices for research markets")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research", help="Research data root")
    parser.add_argument("--categories", type=str, default=None, help="Comma-separated category dir names (default: all)")
    parser.add_argument("--trades-only", action="store_true", help="Only fetch trades")
    parser.add_argument("--prices-only", action="store_true", help="Only fetch prices")
    parser.add_argument("--price-interval", type=str, default="1h", choices=["1m", "1h", "6h", "1d", "1w", "max"], help="Price series interval")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="Delay between API calls (seconds)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip markets already present in existing trades/prices parquet (resume interrupted run)")
    args = parser.parse_args()

    categories_filter = [c.strip() for c in args.categories.split(",")] if args.categories else None
    research_dir = args.research_dir
    if not research_dir.exists():
        print(f"Research dir not found: {research_dir}")
        return 1

    category_dfs = load_research_markets(research_dir)
    if categories_filter:
        category_dfs = {k: v for k, v in category_dfs.items() if k in categories_filter}
    if not category_dfs:
        print("No markets_filtered.csv found. Run collect_research_by_market_list.py first (with --top-n 500).")
        return 1

    session = requests.Session()
    do_trades = not args.prices_only
    do_prices = not args.trades_only

    for cat_name, df in category_dfs.items():
        out_dir = research_dir / cat_name
        out_dir.mkdir(parents=True, exist_ok=True)
        n_markets = len(df)
        print(f"\n[{cat_name}] {n_markets} markets")

        # Build sets of already-fetched market IDs for resume mode
        done_trade_ids: set = set()
        done_price_ids: set = set()
        if args.resume:
            trades_path = out_dir / "trades.parquet"
            if trades_path.exists():
                try:
                    existing = pd.read_parquet(trades_path, columns=["market_id"])
                    done_trade_ids = set(existing["market_id"].dropna().astype(str).unique())
                    print(f"  Resume trades: {len(done_trade_ids):,} markets already fetched, skipping")
                except Exception as e:
                    print(f"  Resume trades: could not read existing parquet ({e}), fetching all")
            prices_path = out_dir / "prices.parquet"
            if prices_path.exists():
                try:
                    existing = pd.read_parquet(prices_path, columns=["market_id"])
                    done_price_ids = set(existing["market_id"].dropna().astype(str).unique())
                    print(f"  Resume prices: {len(done_price_ids):,} markets already fetched, skipping")
                except Exception as e:
                    print(f"  Resume prices: could not read existing parquet ({e}), fetching all")

        if do_trades:
            all_trades: List[Dict[str, Any]] = []
            flush_every = 100
            for i, row in df.iterrows():
                cid = str(row.get("conditionId", "")).strip()
                if not cid or not cid.startswith("0x"):
                    continue
                if args.resume and cid in done_trade_ids:
                    continue
                trades = fetch_trades_for_market(cid, session)
                all_trades.extend(trades)
                if (i + 1) % 50 == 0:
                    print(f"  Trades: {i + 1}/{n_markets} markets, {len(all_trades):,} total")
                # Flush to disk periodically to limit memory
                if len(all_trades) >= 100_000 or (i + 1) % flush_every == 0 and all_trades:
                    trade_df = pd.DataFrame(all_trades)
                    if "conditionId" in trade_df.columns:
                        trade_df["market_id"] = trade_df["conditionId"]
                    else:
                        trade_df["market_id"] = ""
                    # Parquet needs consistent types (avoid object mix of int/str)
                    if "timestamp" in trade_df.columns:
                        ts = pd.to_numeric(trade_df["timestamp"], errors="coerce")
                        trade_df["timestamp"] = ts.fillna(0).astype("int64")
                    for col in ("size", "price", "outcomeIndex"):
                        if col in trade_df.columns:
                            trade_df[col] = pd.to_numeric(trade_df[col], errors="coerce")
                    out_path = out_dir / "trades.parquet"
                    if out_path.exists():
                        existing = pd.read_parquet(out_path)
                        trade_df = pd.concat([existing, trade_df], ignore_index=True)
                        dup_cols = [c for c in ["transactionHash", "market_id", "proxyWallet", "timestamp"] if c in trade_df.columns]
                        if dup_cols:
                            trade_df = trade_df.drop_duplicates(subset=dup_cols, keep="first")
                    # Ensure parquet-safe dtypes
                    _sanitize_trade_df(trade_df)
                    trade_df.to_parquet(out_path, index=False)
                    all_trades = []
                    print(f"  Flushed {len(trade_df):,} trades to {out_path}")
                time.sleep(args.delay)

            if all_trades:
                trade_df = pd.DataFrame(all_trades)
                if "conditionId" in trade_df.columns:
                    trade_df["market_id"] = trade_df["conditionId"]
                else:
                    trade_df["market_id"] = ""
                if "timestamp" in trade_df.columns:
                    ts = pd.to_numeric(trade_df["timestamp"], errors="coerce")
                    trade_df["timestamp"] = ts.fillna(0).astype("int64")
                for col in ("size", "price", "outcomeIndex"):
                    if col in trade_df.columns:
                        trade_df[col] = pd.to_numeric(trade_df[col], errors="coerce")
                out_path = out_dir / "trades.parquet"
                if out_path.exists():
                    existing = pd.read_parquet(out_path)
                    trade_df = pd.concat([existing, trade_df], ignore_index=True)
                    dup_cols = [c for c in ["transactionHash", "market_id", "proxyWallet", "timestamp"] if c in trade_df.columns]
                    if dup_cols:
                        trade_df = trade_df.drop_duplicates(subset=dup_cols, keep="first")
                _sanitize_trade_df(trade_df)
                trade_df.to_parquet(out_path, index=False)
                print(f"  Wrote {len(trade_df):,} trades -> {out_path}")
            elif not (out_dir / "trades.parquet").exists():
                print(f"  No trades for {cat_name}")

        if do_prices:
            price_rows: List[Dict[str, Any]] = []
            for i, row in df.iterrows():
                cid = str(row.get("conditionId", "")).strip()
                if args.resume and cid in done_price_ids:
                    continue
                tokens = _parse_clob_token_ids(row.get("clobTokenIds"))
                # First token is typically YES outcome
                token_id = tokens[0] if tokens else None
                if not token_id:
                    continue
                history = fetch_price_history(token_id, session, interval=args.price_interval)
                for h in history:
                    price_rows.append({
                        "market_id": cid,
                        "outcome": "YES",
                        "timestamp": h["t"],
                        "price": h["p"],
                    })
                if (i + 1) % 50 == 0:
                    print(f"  Prices: {i + 1}/{n_markets} markets, {len(price_rows):,} rows")
                time.sleep(args.delay)

            if price_rows:
                price_df = pd.DataFrame(price_rows)
                out_path = out_dir / "prices.parquet"
                if out_path.exists() and args.resume:
                    existing_prices = pd.read_parquet(out_path)
                    price_df = pd.concat([existing_prices, price_df], ignore_index=True)
                    price_df = price_df.drop_duplicates(subset=["market_id", "timestamp"], keep="first")
                price_df.to_parquet(out_path, index=False)
                print(f"  Wrote {len(price_df):,} price rows -> {out_path}")
            else:
                print(f"  No price history for {cat_name}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
