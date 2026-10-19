#!/usr/bin/env python3
"""
Fetch Kalshi market data: markets, trades, and price candlesticks.
Standalone script — flushes data to disk IMMEDIATELY as fetched.

Usage:
    python scripts/data/fetch_kalshi_data.py
    python scripts/data/fetch_kalshi_data.py --min-volume 10000
    python scripts/data/fetch_kalshi_data.py --workers 10
"""
import argparse
import gc
import json
import sys
import time
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Line-buffered stdout so output appears immediately
sys.stdout.reconfigure(line_buffering=True)

import requests
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src.trading.arbitrage.kalshi_data import candle_to_row, parse_created_time_to_unix  # noqa: E402
DATA_DIR = PROJECT_ROOT / "data" / "kalshi"
REQUEST_DELAY = 0.03


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "PredictionMarketResearch/1.0"})
    return s


def save_df_parquet(df, path, dedup_col=None, sort_col=None):
    """Save DataFrame to parquet, merging with existing file if present."""
    if df.empty:
        return
    if path.exists():
        try:
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df], ignore_index=True)
            if dedup_col and dedup_col in df.columns:
                df = df.drop_duplicates(subset=[dedup_col], keep="last")
        except Exception:
            pass
    if sort_col and sort_col in df.columns:
        df = df.sort_values(sort_col).reset_index(drop=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path, compression="zstd", compression_level=3)


# ─── Checkpoint ───────────────────────────────────────────────────────────────

def load_checkpoint(path):
    if path.exists():
        try:
            with open(path) as f:
                return set(json.load(f).get("processed_tickers", []))
        except Exception:
            pass
    return set()


def save_checkpoint(path, tickers):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({
            "processed_tickers": sorted(tickers),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "count": len(tickers),
        }, f)
    tmp.replace(path)


# ─── Scan markets (streaming, memory-safe) ────────────────────────────────────

def scan_markets(min_volume, markets_parquet_path):
    """Scan all Kalshi markets. Flush qualifying ones to parquet as found."""
    print(f"\n[1/3] Scanning Kalshi markets (keeping volume >= {min_volume:,.0f})...")
    session = make_session()
    kept = []
    cursor = ""
    scanned = 0
    t0 = time.time()
    last_flush_count = 0

    try:
        while True:
            params = {"limit": 1000}
            if cursor:
                params["cursor"] = cursor
            try:
                r = session.get(f"{KALSHI_API}/markets", params=params, timeout=60)
                if r.status_code == 429:
                    r.close()
                    time.sleep(2)
                    continue
                r.raise_for_status()
                data = r.json()
                r.close()
            except MemoryError:
                print(f"  MemoryError at {scanned:,}. GC + retry...")
                gc.collect()
                session.close()
                session = make_session()
                time.sleep(1)
                continue
            except requests.RequestException as e:
                print(f"  Request error at {scanned:,}: {e}")
                break

            batch = data.get("markets", [])
            new_cursor = data.get("cursor", "")
            del data

            if not batch:
                break

            for m in batch:
                v = m.get("volume", 0) or 0
                if v >= min_volume:
                    kept.append(m)

            scanned += len(batch)
            del batch

            # Flush newly found markets to disk immediately
            if len(kept) > last_flush_count:
                _flush_markets(kept, markets_parquet_path)
                new_found = len(kept) - last_flush_count
                last_flush_count = len(kept)
                print(f"  ** Found {new_found} new market(s)! "
                      f"Total kept: {len(kept)} (flushed to disk)")

            if scanned % 20000 == 0:
                elapsed = time.time() - t0
                rate = scanned / elapsed
                est_total = 320000
                eta = max(0, (est_total - scanned) / rate) if rate > 0 else 0
                print(f"  Scanned {scanned:,}  kept {len(kept)}  "
                      f"{rate:.0f} mkts/s  ~{eta:.0f}s left")
                gc.collect()

            cursor = new_cursor
            if not cursor:
                break
            time.sleep(0.05)
    finally:
        session.close()

    # Final flush
    if kept:
        _flush_markets(kept, markets_parquet_path)

    elapsed = time.time() - t0
    print(f"  Scan done: {scanned:,} scanned, {len(kept)} qualifying in {elapsed:.1f}s")
    return kept


def _flush_markets(markets, path):
    """Write qualifying markets to parquet."""
    rows = [{
        "ticker": m.get("ticker", ""),
        "event_ticker": m.get("event_ticker", ""),
        "title": str(m.get("title", ""))[:200],
        "subtitle": str(m.get("subtitle", ""))[:200],
        "status": m.get("status", ""),
        "result": m.get("result", ""),
        "volume": m.get("volume", 0) or 0,
        "volume_24h": m.get("volume_24h", 0) or 0,
        "last_price": m.get("last_price"),
        "yes_bid": m.get("yes_bid"),
        "yes_ask": m.get("yes_ask"),
        "no_bid": m.get("no_bid"),
        "no_ask": m.get("no_ask"),
        "open_interest": m.get("open_interest", 0) or 0,
        "liquidity": m.get("liquidity", 0) or 0,
        "created_time": m.get("created_time", ""),
        "open_time": m.get("open_time", ""),
        "close_time": m.get("close_time", ""),
        "expiration_time": m.get("expiration_time", ""),
    } for m in markets]
    df = pd.DataFrame(rows)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path, compression="zstd", compression_level=3)


# ─── Resolve series_tickers ──────────────────────────────────────────────────

def resolve_series_tickers(markets):
    """Get series_ticker for each unique event_ticker."""
    event_tickers = list({m.get("event_ticker", "") for m in markets if m.get("event_ticker")})
    if not event_tickers:
        return {}
    print(f"\n[2/3] Resolving series_tickers for {len(event_tickers)} events...")
    session = make_session()
    meta = {}
    for i, et in enumerate(event_tickers):
        try:
            r = session.get(f"{KALSHI_API}/events/{et}", timeout=30)
            if r.status_code == 200:
                edata = r.json().get("event", {})
                meta[et] = {
                    "series_ticker": edata.get("series_ticker", ""),
                    "category": edata.get("category", ""),
                }
                print(f"  {et} -> series={meta[et]['series_ticker']}  cat={meta[et]['category']}")
            elif r.status_code == 429:
                time.sleep(2)
                continue
            r.close()
        except Exception as e:
            print(f"  Failed {et}: {e}")
        time.sleep(0.05)
    session.close()
    print(f"  Resolved {len(meta)}/{len(event_tickers)} events")
    return meta


# ─── Fetch + flush per market (immediate disk writes) ─────────────────────────

def fetch_and_flush_market(market, event_meta, trades_dir, prices_dir, max_trades):
    """Fetch trades + prices for one market, flush to disk IMMEDIATELY."""
    ticker = market.get("ticker", "")
    et = market.get("event_ticker", "")
    em = event_meta.get(et, {})
    series = em.get("series_ticker", "")
    n_trades = 0
    n_candles = 0

    # ── Trades ──
    session = make_session()
    trades = []
    cursor = ""
    try:
        while True:
            params = {"limit": 1000, "ticker": ticker}
            if cursor:
                params["cursor"] = cursor
            try:
                r = session.get(f"{KALSHI_API}/markets/trades", params=params, timeout=60)
                if r.status_code == 429:
                    r.close()
                    time.sleep(2)
                    continue
                r.raise_for_status()
                data = r.json()
                r.close()
            except Exception:
                break
            batch = data.get("trades", [])
            new_cursor = data.get("cursor", "")
            del data
            if not batch:
                break
            trades.extend(batch)
            if max_trades > 0 and len(trades) >= max_trades:
                trades = trades[:max_trades]
                break
            cursor = new_cursor
            if not cursor:
                break
            time.sleep(REQUEST_DELAY)
    finally:
        session.close()

    # Flush trades to per-ticker parquet (immediate)
    if trades:
        trade_rows = []
        for t in trades:
            created = t.get("created_time", "")
            ts_unix = parse_created_time_to_unix(created) or 0
            trade_rows.append({
                "trade_id": t.get("trade_id", ""),
                "ticker": t.get("ticker", ""),
                "event_ticker": et,
                "yes_price": (t.get("yes_price", 0) or 0) / 100.0,
                "no_price": (t.get("no_price", 0) or 0) / 100.0,
                "count": t.get("count", 0) or 0,
                "taker_side": t.get("taker_side", ""),
                "created_time": created,
                "timestamp_unix": ts_unix,
            })
        df = pd.DataFrame(trade_rows)
        path = trades_dir / f"{ticker}.parquet"
        save_df_parquet(df, path, dedup_col="trade_id", sort_col="timestamp_unix")
        n_trades = len(trade_rows)

    # ── Candlestick prices ──
    if series:
        session2 = make_session()
        now = int(time.time())
        start = now - (365 * 24 * 3600)
        try:
            r = session2.get(
                f"{KALSHI_API}/series/{series}/markets/{ticker}/candlesticks",
                params={"start_ts": start, "end_ts": now, "period_interval": 1440},
                timeout=60,
            )
            if r.status_code == 200:
                candles = r.json().get("candlesticks", [])
                r.close()
                if candles:
                    # end_period_ts parsed robustly; empty candles -> NaN (not 0);
                    # rows without a usable timestamp are dropped (never ts=0/1).
                    price_rows = [r for r in (candle_to_row(c, ticker, et) for c in candles) if r is not None]
                    df = pd.DataFrame(price_rows)
                    path = prices_dir / f"{ticker}.parquet"
                    save_df_parquet(df, path, sort_col="timestamp")
                    n_candles = len(price_rows)
            else:
                r.close()
        except Exception:
            pass
        finally:
            session2.close()

    return n_trades, n_candles


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch Kalshi market data")
    parser.add_argument("--min-volume", type=float, default=100000,
                        help="Min volume in contracts (default: 100000)")
    parser.add_argument("--workers", type=int, default=10,
                        help="Parallel workers (default: 10)")
    parser.add_argument("--max-trades", type=int, default=10000,
                        help="Max trades per market (default: 10000)")
    parser.add_argument("--no-trades", action="store_true")
    parser.add_argument("--no-prices", action="store_true")
    args = parser.parse_args()

    t0 = time.time()

    # Create output dirs upfront so they appear in your explorer
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trades_dir = DATA_DIR / "trades"
    trades_dir.mkdir(exist_ok=True)
    prices_dir = DATA_DIR / "prices"
    prices_dir.mkdir(exist_ok=True)
    checkpoint_path = DATA_DIR / "fetch_checkpoint.json"

    print("=" * 70)
    print("KALSHI DATA FETCH")
    print("=" * 70)
    print(f"  min_volume:  {args.min_volume:,.0f}")
    print(f"  workers:     {args.workers}")
    print(f"  max_trades:  {args.max_trades:,}")
    print(f"  output:      {DATA_DIR}")
    print(f"  (data flushes to disk as fetched — check {DATA_DIR.relative_to(PROJECT_ROOT)}/)")

    # Write a status file so you can see progress even if terminal output lags
    status_path = DATA_DIR / "fetch_status.json"
    def write_status(phase, detail=""):
        try:
            with open(status_path, "w") as f:
                json.dump({"phase": phase, "detail": detail,
                           "time": datetime.now(timezone.utc).isoformat()}, f)
        except Exception:
            pass

    write_status("scanning", "Starting market scan...")

    # Step 1: Scan markets (flushes markets.parquet as found)
    markets_path = DATA_DIR / "markets.parquet"
    markets = scan_markets(args.min_volume, markets_path)
    if not markets:
        write_status("done", "No markets found above threshold")
        print("\nNo markets found above volume threshold. Try --min-volume 10000")
        return

    print(f"\n  Qualifying markets:")
    for m in sorted(markets, key=lambda x: x.get("volume", 0), reverse=True):
        print(f"    {m.get('ticker',''):45s}  vol={m.get('volume',0):>10,}  {m.get('status','')}")

    if args.no_trades and args.no_prices:
        write_status("done", f"Markets only. {len(markets)} saved.")
        print(f"\nDone in {time.time()-t0:.1f}s")
        return

    # Step 2: Resolve series_tickers
    write_status("resolving", "Getting event metadata for price history...")
    event_meta = resolve_series_tickers(markets) if not args.no_prices else {}

    # Step 3: Load checkpoint
    processed = load_checkpoint(checkpoint_path)
    remaining = [m for m in markets if m.get("ticker", "") not in processed]
    if len(remaining) < len(markets):
        print(f"\n  Checkpoint: {len(markets) - len(remaining)} already done, "
              f"{len(remaining)} remaining")

    if not remaining:
        write_status("done", "All markets already processed")
        print("\nAll markets already processed. Delete fetch_checkpoint.json to re-fetch.")
        return

    # Step 3: Fetch trades + prices (parallel, flush per-market)
    print(f"\n[3/3] Fetching trades & prices for {len(remaining)} markets "
          f"({args.workers} workers)...")
    write_status("fetching", f"0/{len(remaining)} markets")

    total_trades = 0
    total_candles = 0
    errors = 0
    done = 0
    lock = threading.Lock()

    def worker(m):
        nonlocal total_trades, total_candles, errors, done
        ticker = m.get("ticker", "")
        try:
            nt, nc = fetch_and_flush_market(
                m, event_meta, trades_dir, prices_dir, args.max_trades
            )
            with lock:
                total_trades += nt
                total_candles += nc
                done += 1
                processed.add(ticker)
                save_checkpoint(checkpoint_path, processed)
                elapsed = time.time() - t0
                print(f"  [{done}/{len(remaining)}] {ticker[:42]:42s}  "
                      f"trades={nt:>5}  candles={nc:>4}  "
                      f"| total trades={total_trades:,}  candles={total_candles:,}  "
                      f"({elapsed:.0f}s)")
                write_status("fetching", f"{done}/{len(remaining)} markets, "
                             f"{total_trades:,} trades, {total_candles:,} candles")
        except Exception as e:
            with lock:
                errors += 1
                done += 1
                print(f"  [{done}/{len(remaining)}] ERROR {ticker}: {e}")

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            list(executor.map(worker, remaining))
    except KeyboardInterrupt:
        print("\n  Interrupted! Data already on disk. Re-run to resume.")
        save_checkpoint(checkpoint_path, processed)

    elapsed = time.time() - t0
    write_status("done", f"{len(markets)} markets, {total_trades:,} trades, "
                 f"{total_candles:,} candles in {elapsed:.1f}s")

    print(f"\n{'='*70}")
    print(f"KALSHI FETCH COMPLETE  ({elapsed:.1f}s)")
    print(f"{'='*70}")
    print(f"  Markets:  {len(markets)}")
    print(f"  Trades:   {total_trades:,}")
    print(f"  Candles:  {total_candles:,}")
    print(f"  Errors:   {errors}")
    print(f"  Data:     {DATA_DIR}")

    # List output files
    for subdir in [DATA_DIR, trades_dir, prices_dir]:
        files = list(subdir.glob("*.parquet"))
        if files:
            total_kb = sum(f.stat().st_size for f in files) / 1024
            print(f"\n  {subdir.relative_to(PROJECT_ROOT)}/  ({total_kb:.1f} KB)")
            for f in sorted(files):
                size_kb = f.stat().st_size / 1024
                print(f"    {f.name:40s} {size_kb:>8.1f} KB")


if __name__ == "__main__":
    main()
