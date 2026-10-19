#!/usr/bin/env python3
"""
Live whale trade watcher for Polymarket.

At startup:
  1. Incrementally refreshes local research parquets with new trades (per-market,
     only fetches trades newer than what's already saved).
  2. Loads all unresolved (active, not closed) market IDs from the Gamma API.
  3. Builds the whale set + scores from the updated research data.

Watch loop:
  - Polls data-api.polymarket.com/trades every --interval seconds.
  - Alerts only when a known whale trades in an UNRESOLVED market.
  - With --show-unknown, also alerts on very large trades from unknown wallets.
  - Refreshes the active-market set every 30 minutes automatically.

Usage:
    python scripts/live/watch_whale_trades.py
    python scripts/live/watch_whale_trades.py --min-usd 200 --show-unknown
    python scripts/live/watch_whale_trades.py --categories Finance,Geopolitics
    python scripts/live/watch_whale_trades.py --interval 30 --log alerts.jsonl
    python scripts/live/watch_whale_trades.py --no-refresh   # skip data update
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

import requests
import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# ── ANSI colours ───────────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
MAGENTA = "\033[95m"
DIM     = "\033[2m"


# ── Active market set ──────────────────────────────────────────────────────────

def fetch_active_condition_ids(session: requests.Session) -> Set[str]:
    """
    Return conditionIds for all currently open (closed=False) markets on Polymarket.

    Paginates Gamma API until exhausted.  Typically ~1-3k markets.
    """
    active: Set[str] = set()
    offset = 0
    batch_size = 500

    print("  Fetching unresolved market IDs from Gamma API...", end="", flush=True)
    while True:
        try:
            resp = session.get(
                f"{GAMMA_API}/markets",
                params={"closed": "false", "limit": batch_size, "offset": offset},
                timeout=20,
            )
            resp.raise_for_status()
            batch = resp.json() or []
        except Exception as e:
            print(f"\n  Warning: Gamma fetch error: {e}")
            break

        for m in batch:
            cid = m.get("conditionId", "")
            if cid:
                active.add(str(cid).strip())

        if len(batch) < batch_size:
            break
        offset += batch_size
        time.sleep(0.15)

    print(f" {len(active):,} open markets")
    return active


# ── Incremental research data refresh ─────────────────────────────────────────

def _normalize_trade_row(row: dict, condition_id: str) -> dict:
    row["conditionId"] = condition_id
    row["market_id"]   = condition_id
    for k in ("size", "price", "outcomeIndex"):
        if k in row and row[k] is not None:
            try:
                row[k] = float(row[k])
            except (TypeError, ValueError):
                row[k] = None
    if "timestamp" in row and row["timestamp"] is not None:
        try:
            row["timestamp"] = int(float(row["timestamp"]))
        except (TypeError, ValueError):
            row["timestamp"] = 0
    return row


def _fetch_trades_after(
    condition_id: str,
    after_ts: int,
    session: requests.Session,
    request_delay: float = 0.2,
) -> list:
    """Fetch all trades for one market newer than after_ts (unix seconds)."""
    out = []
    offset = 0
    limit = 5_000
    while True:
        try:
            r = session.get(
                f"{DATA_API}/trades",
                params={"market": condition_id, "limit": limit,
                        "offset": offset, "after": after_ts},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json() or []
        except Exception:
            break

        for row in data:
            _normalize_trade_row(row, condition_id)
        out.extend(data)

        if len(data) < limit:
            break
        offset += limit
        time.sleep(request_delay)
    return out


def refresh_research_trades(
    research_dir: Path,
    categories: Optional[list],
    active_ids: Set[str],
    request_delay: float = 0.2,
) -> None:
    """
    Incrementally update each category's trades.parquet with new trades.

    For each market in markets_filtered.csv:
      - Reads existing parquet, finds the max timestamp we already have.
      - Fetches only trades newer than that timestamp.
      - Merges and saves back.

    Only refreshes markets that are currently open (active_ids).
    """
    try:
        from src.whale_strategy.research_data_loader import get_research_categories
    except ImportError:
        return

    if categories is None:
        categories = get_research_categories(research_dir)

    session = requests.Session()
    session.headers["User-Agent"] = "whale-watcher/1.0"
    total_new = 0

    for cat in categories:
        cat_dir = research_dir / cat
        markets_csv = cat_dir / "markets_filtered.csv"
        trades_path = cat_dir / "trades.parquet"

        if not markets_csv.exists():
            continue

        try:
            markets_df = pd.read_csv(markets_csv, low_memory=False)
            if "conditionId" not in markets_df.columns:
                continue
        except Exception:
            continue

        # Load existing parquet — build per-market max timestamp
        existing_df: Optional[pd.DataFrame] = None
        max_ts_by_market: Dict[str, int] = {}
        if trades_path.exists():
            try:
                existing_df = pd.read_parquet(trades_path)
                if "conditionId" in existing_df.columns and "timestamp" in existing_df.columns:
                    ts_series = pd.to_numeric(existing_df["timestamp"], errors="coerce")
                    max_ts_by_market = (
                        existing_df.assign(_ts=ts_series)
                        .groupby("conditionId")["_ts"]
                        .max()
                        .dropna()
                        .astype(int)
                        .to_dict()
                    )
            except Exception:
                pass

        cond_ids = (
            markets_df["conditionId"]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
            .tolist()
        )
        # Only refresh markets that are currently open
        open_ids = [c for c in cond_ids if c in active_ids]

        if not open_ids:
            print(f"  [{cat}] 0 open markets to refresh")
            continue

        print(f"  [{cat}] refreshing {len(open_ids)} open markets...", end="", flush=True)
        new_rows = []
        for cid in open_ids:
            after_ts = max_ts_by_market.get(cid, 0)
            rows = _fetch_trades_after(cid, after_ts, session, request_delay)
            new_rows.extend(rows)
            time.sleep(request_delay)

        if new_rows:
            new_df = pd.DataFrame(new_rows)
            # String-ify object cols to avoid parquet type conflicts
            for col in new_df.columns:
                if new_df[col].dtype == object:
                    new_df[col] = new_df[col].astype(str)

            if existing_df is not None and not existing_df.empty:
                combined = pd.concat([existing_df, new_df], ignore_index=True)
                # Dedup on transactionHash if present
                if "transactionHash" in combined.columns:
                    combined = combined.drop_duplicates(
                        subset=["transactionHash", "conditionId"], keep="last"
                    )
            else:
                combined = new_df

            combined.to_parquet(trades_path, index=False, compression="snappy")
            total_new += len(new_rows)
            print(f" +{len(new_rows):,} new trades")
        else:
            print(" up to date")

    print(f"  Refresh complete — {total_new:,} new trades added across {len(categories)} categories")


# ── Whale set loading ──────────────────────────────────────────────────────────

def load_whale_set(
    research_dir: Path,
    categories: Optional[list],
    volume_percentile: float = 95.0,
) -> Tuple[Set[str], Dict[str, float], Dict[str, float]]:
    """Build whale set + scores + win rates from research parquets."""
    try:
        from src.whale_strategy.research_data_loader import (
            load_research_trades,
            get_research_categories,
        )
        from src.whale_strategy.whale_surprise import build_surprise_positive_whale_set
    except ImportError as e:
        print(f"Warning: could not import whale modules: {e}")
        return set(), {}, {}

    if categories is None:
        categories = get_research_categories(research_dir)

    all_trades = []
    for cat in categories:
        try:
            df = load_research_trades(research_dir, [cat])
            if not df.empty:
                all_trades.append(df)
        except Exception:
            pass

    if not all_trades:
        print("Warning: no research trades found")
        return set(), {}, {}

    trades_df = pd.concat(all_trades, ignore_index=True)

    # Load resolutions
    resolution_winners: Dict[str, str] = {}
    for res_dir in [research_dir.parent / "poly_cat", research_dir]:
        csv = res_dir / "resolutions.csv"
        if csv.exists():
            try:
                rdf = pd.read_csv(csv)
                if "market_id" in rdf.columns and "winner" in rdf.columns:
                    resolution_winners = dict(
                        zip(rdf["market_id"].astype(str), rdf["winner"].astype(str))
                    )
                    break
            except Exception:
                pass

    cutoff = (
        trades_df["datetime"].max()
        if "datetime" in trades_df.columns
        else pd.Timestamp.now()
    )
    print(f"  Building whale set: {len(trades_df):,} trades, cutoff {cutoff.date()}, "
          f"{len(resolution_winners):,} resolutions")

    whale_set, scores, winrates = build_surprise_positive_whale_set(
        trades_df,
        resolution_winners,
        min_surprise=0.0,
        min_trades=10,
        require_positive_surprise=True,
        volume_percentile=volume_percentile,
        cutoff=cutoff,
        recency_halflife_days=90.0,
        bayes_prior_alpha=2.0,
        bayes_prior_beta=2.0,
    )
    return whale_set, scores, winrates


# ── Polling ────────────────────────────────────────────────────────────────────

def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "whale-watcher/1.0"
    return s


def fetch_recent_trades(session: requests.Session, since_ts: int, limit: int = 500) -> list:
    try:
        resp = session.get(
            f"{DATA_API}/trades",
            params={"limit": limit, "after": since_ts},
            timeout=20,
        )
        resp.raise_for_status()
        trades = resp.json() or []
        trades.sort(key=lambda t: int(t.get("timestamp", 0) or 0))
        return trades
    except Exception as e:
        print(f"{DIM}[poll error] {e}{RESET}", flush=True)
        return []


def parse_trade(raw: dict) -> Optional[dict]:
    try:
        ts = int(float(raw.get("timestamp", 0) or 0))
        if ts > 1e12:
            ts = ts // 1000
        price = float(raw.get("price", 0) or 0)
        size  = float(raw.get("size", 0) or 0)
        if price <= 0 or size <= 0:
            return None

        usd = float(raw.get("usdcSize", raw.get("amount", 0)) or 0)
        if usd == 0:
            usd = price * size

        return {
            "ts":       ts,
            "dt":       datetime.fromtimestamp(ts, tz=timezone.utc),
            "maker":    str(raw.get("proxyWallet", raw.get("maker", "")) or ""),
            "side":     str(raw.get("side", "BUY") or "BUY").upper(),
            "outcome":  str(raw.get("outcome", "") or ""),
            "price":    price,
            "size":     size,
            "usd":      usd,
            "cond_id":  str(raw.get("conditionId", "") or ""),
            "title":    str(raw.get("title", "") or ""),
            "slug":     str(raw.get("eventSlug", raw.get("slug", "")) or ""),
            "tx_hash":  str(raw.get("transactionHash", "") or ""),
        }
    except Exception:
        return None


# ── Alert formatting ───────────────────────────────────────────────────────────

def _tier(score: float) -> str:
    if score >= 8.0:
        return f"{RED}{BOLD}[TIER-1]{RESET}"
    if score >= 7.0:
        return f"{MAGENTA}{BOLD}[TIER-2]{RESET}"
    if score >= 6.0:
        return f"{YELLOW}[TIER-3]{RESET}"
    return f"{DIM}[UNRANKED]{RESET}"


def format_alert(t: dict, score: float, wr: float, is_known: bool) -> str:
    time_s = t["dt"].strftime("%H:%M:%S UTC")
    addr   = t["maker"]
    addr_s = addr[:6] + "…" + addr[-4:] if len(addr) > 12 else addr

    outcome = t["outcome"].upper() or ("YES" if t["side"] == "BUY" else "NO")
    side_color = GREEN if outcome in ("YES", "UP", "TRUE") else RED
    side_s = f"{side_color}{BOLD}{outcome}{RESET}"

    tag = f"{CYAN}{BOLD}[WHALE]{RESET}" if is_known else f"{YELLOW}[LARGE]{RESET}"
    tier_s = _tier(score) if score > 0 else ""
    title  = (t["title"] or t["cond_id"])[:60]
    url    = f"https://polymarket.com/event/{t['slug']}" if t["slug"] else ""

    lines = [
        f"{BOLD}{time_s}{RESET}  {tag} {tier_s}",
        f"  ${t['usd']:>9,.0f}  {side_s} @ {t['price']*100:.1f}¢  |  {addr_s}",
        f"  {title}",
    ]
    if score > 0:
        lines.append(f"  {DIM}score={score:.2f}  hist_wr={wr:.0%}{RESET}")
    if url:
        lines.append(f"  {DIM}{url}{RESET}")
    return "\n".join(lines)


# ── Main watch loop ────────────────────────────────────────────────────────────

def watch(
    whale_set: Set[str],
    scores: Dict[str, float],
    winrates: Dict[str, float],
    active_ids: Set[str],
    session: requests.Session,
    min_usd: float = 500.0,
    poll_interval: int = 60,
    show_unknown: bool = False,
    unknown_min_usd: float = 5_000.0,
    log_path: Optional[Path] = None,
    active_refresh_mins: int = 30,
) -> None:
    log_fh = open(log_path, "a") if log_path else None
    seen_hashes: Set[str] = set()
    last_ts = int(time.time()) - 2 * poll_interval
    last_active_refresh = time.time()
    alerts_total = 0
    whale_set_lower = {w.lower() for w in whale_set}

    print(f"\n{'='*65}")
    print(f"  Whale Watcher  —  live Polymarket feed")
    print(f"{'='*65}")
    print(f"  Known whales:        {len(whale_set):,}")
    print(f"  Open markets:        {len(active_ids):,}")
    print(f"  Min USD (whale):     ${min_usd:,.0f}")
    if show_unknown:
        print(f"  Min USD (unknown):   ${unknown_min_usd:,.0f}")
    print(f"  Poll interval:       {poll_interval}s")
    print(f"  Active refresh:      every {active_refresh_mins}m")
    print(f"  Log:                 {log_path or 'none'}")
    print(f"{'='*65}\n")

    try:
        while True:
            poll_start = time.time()

            # Refresh active-market set periodically
            if time.time() - last_active_refresh > active_refresh_mins * 60:
                print(f"{DIM}[{datetime.now().strftime('%H:%M:%S')}] refreshing open market list...{RESET}")
                fresh = fetch_active_condition_ids(session)
                if fresh:
                    active_ids.clear()
                    active_ids.update(fresh)
                last_active_refresh = time.time()

            trades_raw = fetch_recent_trades(session, since_ts=last_ts)

            new_last_ts = last_ts
            for raw in trades_raw:
                t = parse_trade(raw)
                if t is None:
                    continue
                if t["ts"] > new_last_ts:
                    new_last_ts = t["ts"]

                # Dedup
                if t["tx_hash"]:
                    if t["tx_hash"] in seen_hashes:
                        continue
                    seen_hashes.add(t["tx_hash"])
                    if len(seen_hashes) > 50_000:
                        seen_hashes.clear()

                # ── Key filter: only unresolved markets ──────────────────────
                if t["cond_id"] and t["cond_id"] not in active_ids:
                    continue

                maker_lc = t["maker"].lower()
                is_known = maker_lc in whale_set_lower
                score    = scores.get(t["maker"], scores.get(maker_lc, 0.0))
                wr       = winrates.get(t["maker"], winrates.get(maker_lc, 0.5))

                if is_known and t["usd"] >= min_usd:
                    pass  # alert
                elif show_unknown and not is_known and t["usd"] >= unknown_min_usd:
                    score, wr = 0.0, 0.5
                else:
                    continue

                alert_s = format_alert(t, score, wr, is_known)
                print(alert_s)
                print()
                alerts_total += 1

                if log_fh:
                    log_fh.write(json.dumps({
                        "ts": t["ts"],
                        "is_known_whale": is_known,
                        "maker": t["maker"],
                        "side": t["side"],
                        "outcome": t["outcome"],
                        "price": t["price"],
                        "usd": t["usd"],
                        "score": score,
                        "winrate": wr,
                        "cond_id": t["cond_id"],
                        "market_title": t["title"],
                        "market_slug": t["slug"],
                    }) + "\n")
                    log_fh.flush()

            last_ts = new_last_ts
            elapsed = time.time() - poll_start
            ts_s = datetime.now().strftime("%H:%M:%S")
            print(
                f"{DIM}[{ts_s}] {len(trades_raw)} trades polled "
                f"({elapsed:.1f}s)  alerts={alerts_total}  "
                f"open_mkts={len(active_ids):,}  "
                f"next in {max(0, poll_interval - elapsed):.0f}s{RESET}",
                flush=True,
            )
            time.sleep(max(0, poll_interval - elapsed))

    except KeyboardInterrupt:
        print(f"\n\nStopped. Total alerts: {alerts_total}")
    finally:
        if log_fh:
            log_fh.close()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live Polymarket whale trade watcher (unresolved markets only)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--research-dir", type=Path,
                        default=_project_root / "data" / "research")
    parser.add_argument("--categories", default=None,
                        help="Comma-separated categories (default: all)")
    parser.add_argument("--min-usd", type=float, default=500.0,
                        help="Min USD to alert on for known whales")
    parser.add_argument("--show-unknown", action="store_true",
                        help="Also alert on large trades from unlisted wallets")
    parser.add_argument("--unknown-min-usd", type=float, default=5_000.0,
                        help="Min USD threshold for unknown-wallet alerts")
    parser.add_argument("--interval", type=int, default=60,
                        help="Poll interval in seconds")
    parser.add_argument("--active-refresh-mins", type=int, default=30,
                        help="How often (minutes) to re-fetch the open market list")
    parser.add_argument("--log", type=Path, default=None,
                        help="Append alerts as JSONL to this file")
    parser.add_argument("--no-refresh", action="store_true",
                        help="Skip incremental trade data refresh at startup")
    parser.add_argument("--volume-percentile", type=float, default=95.0,
                        help="Whale qualification percentile")
    parser.add_argument("--no-load-whales", action="store_true",
                        help="Skip loading whale set (useful with --show-unknown only)")
    args = parser.parse_args()

    categories = (
        [c.strip() for c in args.categories.split(",")]
        if args.categories else None
    )

    session = _get_session()

    # ── Step 1: Fetch current open markets ────────────────────────────────────
    print("\n[1/3] Loading open (unresolved) market IDs...")
    active_ids = fetch_active_condition_ids(session)
    if not active_ids:
        print("Warning: could not load open markets — will not filter by resolution status")

    # ── Step 2: Refresh research trades ───────────────────────────────────────
    if not args.no_refresh and not args.no_load_whales:
        print("\n[2/3] Refreshing research trade data (new trades only)...")
        refresh_research_trades(
            research_dir=args.research_dir,
            categories=categories,
            active_ids=active_ids,
        )
    else:
        print("\n[2/3] Skipping trade refresh (--no-refresh or --no-load-whales)")

    # ── Step 3: Build whale set ────────────────────────────────────────────────
    whale_set: Set[str] = set()
    scores: Dict[str, float] = {}
    winrates: Dict[str, float] = {}

    if not args.no_load_whales:
        print("\n[3/3] Building whale set from updated research data...")
        whale_set, scores, winrates = load_whale_set(
            research_dir=args.research_dir,
            categories=categories,
            volume_percentile=args.volume_percentile,
        )
        print(f"  {len(whale_set):,} qualified whales  "
              f"(scored: {len(scores):,})\n")
    else:
        print("\n[3/3] Skipped (--no-load-whales)\n")

    if not whale_set and not args.show_unknown:
        print("No whales loaded and --show-unknown not set. Nothing to watch.")
        print("Try --show-unknown to alert on all large trades, or check --research-dir.")
        return 1

    # ── Watch ──────────────────────────────────────────────────────────────────
    watch(
        whale_set=whale_set,
        scores=scores,
        winrates=winrates,
        active_ids=active_ids,
        session=session,
        min_usd=args.min_usd,
        poll_interval=args.interval,
        show_unknown=args.show_unknown,
        unknown_min_usd=args.unknown_min_usd,
        log_path=args.log,
        active_refresh_mins=args.active_refresh_mins,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
