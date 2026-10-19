#!/usr/bin/env python3
"""
Live Whale Tracker (Polymarket CLOB)

Tracks known whale addresses in near-real time by polling the CLOB trades
endpoint. Requires Polymarket CLOB API credentials (L2 auth).

Examples:
    # Load whales from DB (top 25 by volume)
    python scripts/live_whale_tracker.py --db-path data/prediction_markets.db --max-whales 25

    # Use a watchlist file (one address per line)
    python scripts/live_whale_tracker.py --whales-file data/whales.txt --min-usd 1000

    # Track only taker-side trades, show leaderboard every 5 minutes
    python scripts/live_whale_tracker.py --role taker --leaderboard-interval 300
"""

import argparse
import hmac
import hashlib
import json
import os
import time
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Set, Tuple
from collections import defaultdict, deque

import requests

CLOB_API = "https://clob.polymarket.com"

DEFAULT_DB_PATH = "data/prediction_markets.db"
DEFAULT_LOG_PATH = "data/live_whale_trades.jsonl"


@dataclass
class TradeEvent:
    trade_id: str
    whale_address: str
    role: str
    market_id: Optional[str]
    asset_id: Optional[str]
    side: str
    price: float
    size: float
    notional_usd: float
    outcome: Optional[str]
    ts: float
    raw: Dict


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_timestamp(value: object) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:  # likely ms
            ts = ts / 1000.0
        return ts
    if isinstance(value, str):
        try:
            ts = float(value)
            if ts > 1e12:
                ts = ts / 1000.0
            return ts
        except ValueError:
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt.timestamp()
            except ValueError:
                return time.time()
    return time.time()


def short_addr(address: str, head: int = 8, tail: int = 6) -> str:
    if not address:
        return "unknown"
    if len(address) <= head + tail:
        return address
    return f"{address[:head]}...{address[-tail:]}"


class L2Auth:
    """
    Polymarket CLOB L2 auth headers.

    Signature format: HMAC_SHA256(secret, timestamp + method + path + body)
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        address: Optional[str] = None,
        header_style: str = "both",
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.address = address
        self.header_style = header_style

    def sign_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers: Dict[str, str] = {}

        if self.header_style in ("hyphen", "both"):
            headers.update({
                "POLY-API-KEY": self.api_key,
                "POLY-PASSPHRASE": self.passphrase,
                "POLY-TIMESTAMP": timestamp,
                "POLY-SIGNATURE": signature,
            })
        if self.header_style in ("underscore", "both"):
            headers.update({
                "POLY_API_KEY": self.api_key,
                "POLY_PASSPHRASE": self.passphrase,
                "POLY_TIMESTAMP": timestamp,
                "POLY_SIGNATURE": signature,
            })
        if self.address:
            headers["POLY_ADDRESS"] = self.address

        return headers


class TradeDeduper:
    def __init__(self, max_size: int = 20000):
        self.max_size = max_size
        self._seen: Set[str] = set()
        self._order: Deque[str] = deque()

    def is_new(self, trade_id: str) -> bool:
        if trade_id in self._seen:
            return False
        self._seen.add(trade_id)
        self._order.append(trade_id)
        if len(self._order) > self.max_size:
            old = self._order.popleft()
            self._seen.discard(old)
        return True


class MarketLookup:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        self.cache: Dict[str, str] = {}

        if self.db_path.exists():
            self.conn = sqlite3.connect(str(self.db_path))

    def get_label(self, market_id: Optional[str]) -> Optional[str]:
        if not market_id:
            return None
        if market_id in self.cache:
            return self.cache[market_id]
        if not self.conn:
            return None
        row = self.conn.execute(
            "SELECT question, slug FROM polymarket_markets WHERE id = ?",
            (market_id,),
        ).fetchone()
        if not row:
            return None
        question, slug = row
        label = question or slug
        if label:
            self.cache[market_id] = label
        return label

    def close(self) -> None:
        if self.conn:
            self.conn.close()


class WhaleLeaderboard:
    def __init__(self):
        self.counts: Dict[str, int] = defaultdict(int)
        self.volume: Dict[str, float] = defaultdict(float)

    def record(self, event: TradeEvent) -> None:
        self.counts[event.whale_address] += 1
        self.volume[event.whale_address] += event.notional_usd

    def render(self, labels: Dict[str, str], top_n: int = 10) -> List[str]:
        rows = sorted(
            self.volume.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:top_n]
        lines = ["WHALE LEADERBOARD (volume)"]
        for i, (addr, vol) in enumerate(rows, 1):
            label = labels.get(addr) or short_addr(addr)
            count = self.counts.get(addr, 0)
            lines.append(f"{i:>2}. {label:<18}  ${vol:,.0f}  trades={count}")
        return lines


def parse_addresses_file(path: Path) -> Tuple[List[str], Dict[str, str]]:
    addresses: List[str] = []
    labels: Dict[str, str] = {}
    if not path.exists():
        return addresses, labels
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        label = ""
        if "," in line:
            address, label = (part.strip() for part in line.split(",", 1))
        elif " " in line:
            address, label = (part.strip() for part in line.split(None, 1))
        else:
            address = line
        if address:
            addresses.append(address)
            if label:
                labels[address] = label
    return addresses, labels


def load_whales_from_db(
    db_path: str,
    method: str,
    role: str,
    min_trades: int,
    min_volume: float,
    max_whales: int,
    sort_by: str,
) -> List[str]:
    import sys

    # src/whale_strategy imports its legacy siblings by bare name (`trading`, `data_quality`)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from src.whale_strategy.polymarket_whales import (
        load_polymarket_trades,
        identify_polymarket_whales,
        calculate_trader_stats,
    )

    trades = load_polymarket_trades(db_path)
    whales = identify_polymarket_whales(
        trades,
        method=method,
        role=role,
        min_trades=min_trades,
        min_volume=min_volume,
    )
    if not whales:
        return []

    stats = calculate_trader_stats(trades, role=role)
    stats = stats[stats["address"].isin(whales)]
    if sort_by in stats.columns:
        stats = stats.sort_values(sort_by, ascending=False)
    if max_whales > 0:
        stats = stats.head(max_whales)
    return stats["address"].tolist()


def build_trade_event(
    trade: Dict,
    whale_address: str,
    role: str,
    min_usd: float,
) -> Optional[TradeEvent]:
    trade_id = str(trade.get("id") or trade.get("trade_id") or "")
    if not trade_id:
        trade_id = f"{whale_address}:{trade.get('match_time')}:{trade.get('price')}:{trade.get('size')}"

    price = parse_float(trade.get("price"), 0.0)
    size = parse_float(trade.get("size"), 0.0)
    notional = parse_float(trade.get("amount") or trade.get("usd_amount"), 0.0)
    if notional == 0 and price and size:
        notional = price * size

    if notional < min_usd:
        return None

    ts = parse_timestamp(trade.get("match_time") or trade.get("timestamp"))

    return TradeEvent(
        trade_id=trade_id,
        whale_address=whale_address,
        role=role,
        market_id=trade.get("market") or trade.get("market_id"),
        asset_id=trade.get("asset_id") or trade.get("token_id"),
        side=str(trade.get("side") or trade.get("taker_side") or "").upper(),
        price=price,
        size=size,
        notional_usd=notional,
        outcome=trade.get("outcome"),
        ts=ts,
        raw=trade,
    )


def format_trade(
    event: TradeEvent,
    labels: Dict[str, str],
    market_lookup: Optional[MarketLookup] = None,
) -> str:
    ts = datetime.fromtimestamp(event.ts, tz=timezone.utc).strftime("%H:%M:%S")
    label = labels.get(event.whale_address) or short_addr(event.whale_address)
    market_label = None
    if market_lookup:
        market_label = market_lookup.get_label(event.market_id)
    market_display = market_label or short_addr(event.market_id or "")
    outcome = f" {event.outcome}" if event.outcome else ""
    return (
        f"[{ts} UTC] {label} ({event.role}) "
        f"{event.side or 'TRADE':<5} ${event.notional_usd:,.0f} "
        f"@ {event.price:.4f} size={event.size:,.2f}{outcome} "
        f"market={market_display}"
    )


class WhaleTradePoller:
    def __init__(
        self,
        session: requests.Session,
        auth: L2Auth,
        addresses: List[str],
        roles: List[str],
        min_usd: float,
        request_delay: float,
        market: Optional[str],
        cursor_backoff: int,
    ):
        self.session = session
        self.auth = auth
        self.addresses = addresses
        self.roles = roles
        self.min_usd = min_usd
        self.request_delay = request_delay
        self.market = market
        self.cursor_backoff = cursor_backoff
        self.cursors: Dict[Tuple[str, str], float] = {}
        self.deduper = TradeDeduper()

    def _init_cursor(self, address: str, role: str, start_ts: float) -> None:
        self.cursors[(address, role)] = start_ts

    def fetch_trades(self, address: str, role: str) -> List[Dict]:
        after_ts = self.cursors.get((address, role), 0)
        params: List[Tuple[str, str]] = [(role, address)]
        if self.market:
            params.append(("market", self.market))
        if after_ts > 0:
            params.append(("after", str(int(after_ts))))

        req = requests.Request("GET", f"{CLOB_API}/data/trades", params=params)
        prepped = self.session.prepare_request(req)
        headers = self.auth.sign_headers("GET", prepped.path_url)
        prepped.headers.update(headers)

        response = self.session.send(prepped, timeout=10)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        if isinstance(data, dict) and "data" in data:
            return data["data"] or []
        if isinstance(data, list):
            return data
        return []

    def poll_once(self) -> List[TradeEvent]:
        events: List[TradeEvent] = []

        for address in self.addresses:
            for role in self.roles:
                try:
                    trades = self.fetch_trades(address, role)
                except Exception as exc:
                    print(f"Fetch error for {short_addr(address)} ({role}): {exc}")
                    continue

                max_ts = self.cursors.get((address, role), 0)
                for trade in trades:
                    event = build_trade_event(trade, address, role, self.min_usd)
                    if not event:
                        continue
                    max_ts = max(max_ts, event.ts)
                    if self.deduper.is_new(event.trade_id):
                        events.append(event)

                if max_ts > 0:
                    self.cursors[(address, role)] = max(0, max_ts - self.cursor_backoff)

                time.sleep(self.request_delay)

        return events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live whale address tracker (Polymarket CLOB).")
    parser.add_argument("--db-path", type=str, default=DEFAULT_DB_PATH)
    parser.add_argument("--whales-file", type=str, default="")
    parser.add_argument("--addresses", type=str, default="")
    parser.add_argument("--whale-method", type=str, default="win_rate_60pct")
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--min-volume", type=float, default=1000)
    parser.add_argument("--max-whales", type=int, default=25)
    parser.add_argument("--sort-by", type=str, default="total_volume")
    parser.add_argument("--role", type=str, choices=["maker", "taker", "both"], default="both")
    parser.add_argument("--min-usd", type=float, default=500.0)
    parser.add_argument("--market", type=str, default="")
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument("--lookback-mins", type=float, default=5.0)
    parser.add_argument("--cursor-backoff", type=int, default=1)
    parser.add_argument("--leaderboard-interval", type=int, default=300)
    parser.add_argument("--log-path", type=str, default=DEFAULT_LOG_PATH)
    parser.add_argument("--no-log", action="store_true")
    parser.add_argument("--header-style", type=str, choices=["hyphen", "underscore", "both"], default="both")
    parser.add_argument("--no-db-market-labels", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_env_file(Path(".env"))
    args = parse_args()

    api_key = os.environ.get("POLYMARKET_API_KEY", "")
    api_secret = os.environ.get("POLYMARKET_API_SECRET", "")
    passphrase = os.environ.get("POLYMARKET_PASSPHRASE", "")
    address = os.environ.get("POLYMARKET_ADDRESS") or os.environ.get("POLY_ADDRESS")

    if not (api_key and api_secret and passphrase):
        raise SystemExit(
            "Missing CLOB credentials. Set POLYMARKET_API_KEY, "
            "POLYMARKET_API_SECRET, and POLYMARKET_PASSPHRASE."
        )

    auth = L2Auth(
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        address=address,
        header_style=args.header_style,
    )

    addresses: List[str] = []
    labels: Dict[str, str] = {}

    if args.whales_file:
        file_addrs, file_labels = parse_addresses_file(Path(args.whales_file))
        addresses.extend(file_addrs)
        labels.update(file_labels)

    if args.addresses:
        addrs = [a.strip() for a in args.addresses.split(",") if a.strip()]
        addresses.extend(addrs)

    if not addresses:
        addresses = load_whales_from_db(
            db_path=args.db_path,
            method=args.whale_method,
            role="maker",
            min_trades=args.min_trades,
            min_volume=args.min_volume,
            max_whales=args.max_whales,
            sort_by=args.sort_by,
        )
        if not addresses:
            raise SystemExit("No whale addresses loaded. Provide --whales-file or --addresses.")
    else:
        # Preserve order while removing duplicates
        addresses = list(dict.fromkeys(addresses))

    role_list = ["maker", "taker"] if args.role == "both" else [args.role]

    market_lookup = None
    if not args.no_db_market_labels and Path(args.db_path).exists():
        market_lookup = MarketLookup(args.db_path)

    poller = WhaleTradePoller(
        session=requests.Session(),
        auth=auth,
        addresses=addresses,
        roles=role_list,
        min_usd=args.min_usd,
        request_delay=args.request_delay,
        market=args.market or None,
        cursor_backoff=args.cursor_backoff,
    )

    start_ts = time.time() - (args.lookback_mins * 60)
    for address in addresses:
        for role in role_list:
            poller._init_cursor(address, role, start_ts)

    leaderboard = WhaleLeaderboard()
    last_board = time.time()
    log_path = Path(args.log_path)
    print(f"Tracking {len(addresses)} whales (role={args.role})")
    print(f"Min trade: ${args.min_usd:,.0f} | Poll: {args.poll_interval:.1f}s")

    try:
        while True:
            events = poller.poll_once()
            for event in sorted(events, key=lambda e: e.ts):
                print(format_trade(event, labels, market_lookup))
                leaderboard.record(event)
                if not args.no_log:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(log_path, "a", encoding="utf-8") as f:
                        payload = dict(event.raw)
                        payload["_whale_address"] = event.whale_address
                        payload["_role"] = event.role
                        payload["_notional_usd"] = event.notional_usd
                        payload["_timestamp"] = event.ts
                        f.write(json.dumps(payload) + "\n")

            now = time.time()
            if args.leaderboard_interval > 0 and (now - last_board) >= args.leaderboard_interval:
                for line in leaderboard.render(labels):
                    print(line)
                last_board = now

            if args.once:
                break

            time.sleep(args.poll_interval)
    finally:
        if market_lookup:
            market_lookup.close()


if __name__ == "__main__":
    main()
