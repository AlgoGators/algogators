"""Parquet sinks.

Layout::

    {root}/{venue}/{YYYY-MM-DD}/books-{HH}.parquet          # compacted hour
    {root}/{venue}/{YYYY-MM-DD}/books-{HH}.partNNNN.parquet # flushed, not yet compacted
    {root}/{venue}/{YYYY-MM-DD}/trades-{HH}.parquet
    {root}/{venue}/{YYYY-MM-DD}/bbo-{HH}.parquet      # venue-published best bid/ask events
    {root}/{venue}/{YYYY-MM-DD}/prices-{HH}.parquet   # reference prices (polymarket_rtds)
    {root}/{venue}/{YYYY-MM-DD}/markets-{HH}.parquet  # discovered-market metadata (add/remove)
    {root}/kalshi/{YYYY-MM-DD}/status-{HH}.parquet    # market status/result changes (settlement log)
    {root}/kalshi/{YYYY-MM-DD}/lip-{HH}.parquet       # liquidity-incentive programme snapshots (k-lip)

Each flush writes small, self-contained part files (so a crash never corrupts more
than the in-memory buffer). Once an hour (UTC, by local receive time) is over,
its parts are merged into a single hourly file. Readers can simply glob
``*.parquet`` in a date directory -- parts and compacted files never overlap.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger("recorder.writer")

_STR = pa.dictionary(pa.int32(), pa.string())

BOOK_SCHEMA = pa.schema(
    [
        ("venue", _STR),
        ("market_id", _STR),
        ("outcome", _STR),
        ("pair", _STR),
        ("source", _STR),
        ("ts_exchange_ns", pa.int64()),  # exchange timestamp (null if venue gives none)
        ("ts_recv_ns", pa.int64()),  # local wall clock at sample, UTC ns
        ("seq", pa.int64()),
        ("n_updates", pa.int32()),  # book changes since previous row
        ("gap", pa.bool_()),  # sequence gap / resync happened since previous row
        ("best_bid", pa.float64()),
        ("best_ask", pa.float64()),
        ("bid_px", pa.list_(pa.float64())),
        ("bid_sz", pa.list_(pa.float64())),
        ("ask_px", pa.list_(pa.float64())),
        ("ask_sz", pa.list_(pa.float64())),
        # added 2026-10-14; null in files written before that
        ("last_update_ns", pa.int64()),  # local recv time of the venue's last message for this market
        ("age_s", pa.float32()),  # ts_recv_ns - last_update_ns, seconds
    ]
)

TRADE_SCHEMA = pa.schema(
    [
        ("venue", _STR),
        ("market_id", _STR),
        ("outcome", _STR),
        ("pair", _STR),
        ("trade_id", pa.string()),
        ("ts_exchange_ns", pa.int64()),
        ("ts_recv_ns", pa.int64()),
        ("price", pa.float64()),
        ("size", pa.float64()),
        ("taker_side", _STR),
        ("source", _STR),
    ]
)

BBO_SCHEMA = pa.schema(
    [
        ("venue", _STR),
        ("market_id", _STR),
        ("outcome", _STR),
        ("pair", _STR),
        ("ts_exchange_ns", pa.int64()),
        ("ts_recv_ns", pa.int64()),
        ("best_bid", pa.float64()),
        ("best_ask", pa.float64()),
        ("source", _STR),
    ]
)

# Reference prices (Polymarket RTDS: Chainlink / Binance crypto prices).
PRICE_SCHEMA = pa.schema(
    [
        ("venue", _STR),
        ("topic", _STR),
        ("symbol", _STR),
        ("ts_exchange_ns", pa.int64()),  # price observation time (payload timestamp)
        ("ts_msg_ns", pa.int64()),  # socket message timestamp
        ("ts_recv_ns", pa.int64()),
        ("value", pa.float64()),
    ]
)

# One row each time a discovered market rolls in or out: maps ids to group / series /
# event / slug / strike so analysis can join without calling the API again.
MARKET_SCHEMA = pa.schema(
    [
        ("venue", _STR),
        ("market_id", _STR),
        ("group", _STR),
        ("series", _STR),
        ("event", pa.string()),
        ("title", pa.string()),
        ("strike_type", _STR),
        ("floor_strike", pa.float64()),
        ("cap_strike", pa.float64()),
        ("close_ns", pa.int64()),
        ("outcome", _STR),
        ("ts_recv_ns", pa.int64()),
        ("action", _STR),  # add | remove
    ]
)

# Kalshi market status / result changes (close + settlement logging, src/recorder/settlement.py).
STATUS_SCHEMA = pa.schema(
    [
        ("venue", _STR),
        ("market_id", _STR),
        ("group", _STR),
        ("event", pa.string()),
        ("status", _STR),
        ("result", _STR),
        ("prev_status", _STR),  # null on a first_seen row
        ("prev_result", _STR),
        ("close_ns", pa.int64()),
        ("settlement_ns", pa.int64()),  # venue settlement_ts (null until settled)
        ("expiration_value", pa.string()),
        ("settlement_value", pa.float64()),
        ("first_seen", pa.bool_()),  # baseline row, not a change
        ("ts_recv_ns", pa.int64()),
    ]
)

# Kalshi Liquidity Incentive Program snapshots (incentives discovery groups, e.g. k-lip): one row per
# programme per refresh, for every active programme and every upcoming one starting within the
# group's lookahead. ``selected`` = the market was in the group's recorded set after that refresh.
LIP_SCHEMA = pa.schema(
    [
        ("venue", _STR),
        ("group", _STR),
        ("program_id", pa.string()),
        ("market_id", _STR),
        ("status", _STR),  # active | upcoming (as queried)
        ("start_ns", pa.int64()),
        ("end_ns", pa.int64()),
        ("period_reward_usd", pa.float64()),  # API period_reward is in centi-cents
        ("reward_usd_day", pa.float64()),  # period_reward spread uniformly over the period
        ("target_size", pa.float64()),
        ("discount_factor_bps", pa.int32()),
        ("description", _STR),
        ("paid_out", pa.bool_()),
        ("selected", pa.bool_()),
        ("rank", pa.int32()),  # market rank by reward $/day in this refresh (1 = largest)
        ("ts_recv_ns", pa.int64()),
    ]
)

SCHEMAS = {"books": BOOK_SCHEMA, "trades": TRADE_SCHEMA, "bbo": BBO_SCHEMA,
           "prices": PRICE_SCHEMA, "markets": MARKET_SCHEMA, "status": STATUS_SCHEMA, "lip": LIP_SCHEMA}
_PART_RE = re.compile(r"^(books|trades|bbo|prices|markets|status|lip)-(\d{2})\.part(\d{4})\.parquet$")
_NS = 1_000_000_000


def conform(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Cast ``table`` to ``schema``, adding null columns that older files lack
    (e.g. book parts written before ``last_update_ns``/``age_s`` existed)."""
    cols = []
    for f in schema:
        if f.name in table.column_names:
            cols.append(table[f.name])
        else:
            cols.append(pa.nulls(table.num_rows, type=f.type))
    return pa.Table.from_arrays(cols, schema=schema)


def _hour_key(ts_ns: int) -> Tuple[str, str]:
    dt = datetime.fromtimestamp(ts_ns / _NS, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H")


class CountingSink:
    """Dry-run sink: counts rows, keeps the latest row per market, writes nothing."""

    def __init__(self) -> None:
        self.counts: Dict[str, Counter] = {k: Counter() for k in SCHEMAS}
        self.latest: Dict[Tuple[str, str], dict] = {}
        self._pending = 0

    def add_book(self, row: dict) -> None:
        self.counts["books"][row["venue"]] += 1
        self.latest[(row["venue"], row["market_id"])] = row
        self._pending += 1

    def add_trade(self, row: dict) -> None:
        self.counts["trades"][row["venue"]] += 1
        self._pending += 1

    def add_bbo(self, row: dict) -> None:
        self.counts["bbo"][row["venue"]] += 1
        self._pending += 1

    def add_price(self, row: dict) -> None:
        self.counts["prices"][row["venue"]] += 1
        self.latest[(row["venue"], f"{row['topic']}:{row['symbol']}")] = row
        self._pending += 1

    def add_market(self, row: dict) -> None:
        self.counts["markets"][row["venue"]] += 1
        self._pending += 1

    def add_status(self, row: dict) -> None:
        self.counts["status"][row["venue"]] += 1
        self._pending += 1

    def add_lip(self, row: dict) -> None:
        self.counts["lip"][row["venue"]] += 1
        self._pending += 1

    def flush(self) -> int:
        n, self._pending = self._pending, 0
        return n

    def compact_completed(self, now_ns: int) -> int:
        return 0

    def close(self, now_ns: int | None = None) -> int:
        return self.flush()


class ParquetSink:
    def __init__(self, root: os.PathLike | str, compression: str = "zstd", max_buffer_rows: int = 200_000):
        self.root = Path(root)
        self.compression = compression
        self.max_buffer_rows = max_buffer_rows
        self._buf: Dict[Tuple[str, str], List[dict]] = defaultdict(list)  # (kind, venue) -> rows
        self.counts: Dict[str, Counter] = {k: Counter() for k in SCHEMAS}
        self.files_written = 0

    # -- buffering -----------------------------------------------------------

    def add_book(self, row: dict) -> None:
        self._buf[("books", row["venue"])].append(row)

    def add_trade(self, row: dict) -> None:
        self._buf[("trades", row["venue"])].append(row)

    def add_bbo(self, row: dict) -> None:
        self._buf[("bbo", row["venue"])].append(row)

    def add_price(self, row: dict) -> None:
        self._buf[("prices", row["venue"])].append(row)

    def add_market(self, row: dict) -> None:
        self._buf[("markets", row["venue"])].append(row)

    def add_status(self, row: dict) -> None:
        self._buf[("status", row["venue"])].append(row)

    def add_lip(self, row: dict) -> None:
        self._buf[("lip", row["venue"])].append(row)

    @property
    def buffered(self) -> int:
        return sum(len(v) for v in self._buf.values())

    def needs_flush(self) -> bool:
        return self.buffered >= self.max_buffer_rows

    # -- writing -------------------------------------------------------------

    def _next_part(self, d: Path, kind: str, hour: str) -> Path:
        n = -1
        if d.exists():
            for p in d.iterdir():
                m = _PART_RE.match(p.name)
                if m and m.group(1) == kind and m.group(2) == hour:
                    n = max(n, int(m.group(3)))
        return d / f"{kind}-{hour}.part{n + 1:04d}.parquet"

    def flush(self) -> int:
        """Write all buffered rows to part files. Returns rows written."""
        buf, self._buf = self._buf, defaultdict(list)
        total = 0
        for (kind, venue), rows in buf.items():
            groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
            for r in rows:
                groups[_hour_key(r["ts_recv_ns"])].append(r)
            for (date, hour), grp in sorted(groups.items()):
                d = self.root / venue / date
                d.mkdir(parents=True, exist_ok=True)
                path = self._next_part(d, kind, hour)
                table = pa.Table.from_pylist(grp, schema=SCHEMAS[kind])
                tmp = path.with_suffix(".tmp")
                pq.write_table(table, tmp, compression=self.compression)
                os.replace(tmp, path)
                self.files_written += 1
                self.counts[kind][venue] += len(grp)
                total += len(grp)
        return total

    def compact_completed(self, now_ns: int) -> int:
        """Merge part files of hours strictly before the current UTC hour. Returns hours compacted."""
        cur = _hour_key(now_ns)
        done = 0
        if not self.root.exists():
            return 0
        for venue_dir in (p for p in self.root.iterdir() if p.is_dir()):
            for date_dir in (p for p in venue_dir.iterdir() if p.is_dir()):
                groups: Dict[Tuple[str, str], List[Path]] = defaultdict(list)
                for p in date_dir.iterdir():
                    m = _PART_RE.match(p.name)
                    if m:
                        groups[(m.group(1), m.group(2))].append(p)
                for (kind, hour), parts in groups.items():
                    if (date_dir.name, hour) >= cur:
                        continue
                    target = date_dir / f"{kind}-{hour}.parquet"
                    srcs = ([target] if target.exists() else []) + sorted(parts)
                    tables = [conform(pq.read_table(p), SCHEMAS[kind]) for p in srcs]
                    merged = pa.concat_tables(tables).sort_by("ts_recv_ns")
                    tmp = date_dir / f"{kind}-{hour}.compact.tmp"
                    pq.write_table(merged, tmp, compression=self.compression)
                    os.replace(tmp, target)
                    for p in parts:
                        p.unlink()
                    done += 1
        return done

    def close(self, now_ns: int | None = None) -> int:
        n = self.flush()
        if now_ns is not None:
            try:
                self.compact_completed(now_ns)
            except Exception as e:  # never lose data because compaction failed
                log.warning("compaction failed: %s", e)
        return n
