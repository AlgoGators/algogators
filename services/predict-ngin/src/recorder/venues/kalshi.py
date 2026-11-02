"""Kalshi adapter (read-only).

Verified 2026-10-12 against https://docs.kalshi.com and live endpoints:

* REST ``GET {API}/markets/{ticker}/orderbook?depth=N`` -- public, no auth. Returns
  ``orderbook_fp.{yes_dollars,no_dollars}`` = lists of ``[price_dollars, count]`` (bids
  only, ascending; ``depth=N`` returns the best N per side).
* REST ``GET {API}/markets/trades?ticker=..&min_ts=..`` -- public trade prints.
* WS ``wss://external-api-ws.kalshi.com/trade-api/ws/v2`` -- **requires an API key even for
  public channels** (headers KALSHI-ACCESS-KEY / -SIGNATURE / -TIMESTAMP). Channels
  ``orderbook_delta`` (snapshot + deltas with per-subscription ``seq``) and ``trade``.

Kalshi books are bid-only on both outcomes; a NO bid at p is a YES ask at 1-p.
Everything here is stored in YES terms: bids = YES bids, asks = 1 - NO bids.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Dict, Iterable, List, Optional, Tuple

from predict_ngin.venues.kalshi.endpoints import BASE_URL as API
from predict_ngin.venues.kalshi.endpoints import WS_SIGN_PATH, WS_URL

from ..book import OrderBook, norm_px
from ..core import Events, FeedContext, MarketMeta, trade_row
from ..sequence import SequenceTracker
from ..util import Backoff, TokenBucket, fnum, ms_to_ns, now_ns, rfc3339_to_ns

log = logging.getLogger("recorder.kalshi")

VENUE = "kalshi"
# API, WS_URL and WS_SIGN_PATH come from predict_ngin.venues.kalshi.endpoints (defined once).

Level = Tuple[float, float]

# Frames buffered client-side before websockets stops reading the socket. The default (16)
# made Polymarket drop the connection ('1013 slow consumer') during bursts of the crypto
# up/down books (~1,300 msgs/s) on 2026-10-13.
MAX_QUEUE = 16384


# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------


def _levels(ob: dict, side: str) -> List[Level]:
    """Read one side ('yes'/'no') in any of Kalshi's formats -> [(price_dollars, size)]."""
    for key in (f"{side}_dollars_fp", f"{side}_dollars"):
        if ob.get(key):
            return [(float(p), float(s)) for p, s in ob[key]]
    if ob.get(side):  # legacy integer cents
        return [(float(p) / 100.0, float(s)) for p, s in ob[side]]
    return []


# YES/NO complement conversion lives in predict_ngin.books.book (step 5.7).
from predict_ngin.books.book import to_yes_terms  # noqa: E402,F401


def parse_rest_orderbook(payload: dict) -> Tuple[List[Level], List[Level]]:
    ob = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    return to_yes_terms(_levels(ob, "yes"), _levels(ob, "no"))


def _trade_price(t: dict) -> float:
    if t.get("yes_price_dollars") not in (None, ""):
        return float(t["yes_price_dollars"])
    if t.get("yes_price") is not None:
        return float(t["yes_price"]) / 100.0
    return 0.0


def _trade_size(t: dict) -> float:
    return fnum(t.get("count_fp", t.get("count")))


def parse_rest_trades(payload: dict, recv_ns: int, meta: Optional[Dict[str, MarketMeta]] = None) -> List[dict]:
    rows = []
    for t in payload.get("trades") or []:
        tk = t.get("ticker") or t.get("market_ticker")
        rows.append(
            trade_row(
                VENUE, tk, (meta or {}).get(tk),
                trade_id=str(t.get("trade_id")),
                ts_exchange_ns=rfc3339_to_ns(t.get("created_time")),
                ts_recv_ns=recv_ns,
                price=_trade_price(t),
                size=_trade_size(t),
                taker_side=t.get("taker_side"),
                source="rest",
            )
        )
    return rows


class KalshiWsState:
    """Pure state machine for the Kalshi WS feed (testable without network)."""

    def __init__(self, meta: Optional[Dict[str, MarketMeta]] = None, active: Optional[set] = None):
        self.meta = meta if meta is not None else {}
        self.books: Dict[str, OrderBook] = {}
        self.seq = SequenceTracker()
        self._sid_of: Dict[str, int] = {}
        # When set, messages for tickers outside it are ignored (markets that rolled out
        # can still send a few deltas after delete_markets; they must not force a resync).
        self.active = active
        self.sids: Dict[str, int] = {}  # channel -> sid on the current connection
        self.errors: List[dict] = []

    def drop(self, ticker: str) -> None:
        self.books.pop(ticker, None)
        self._sid_of.pop(ticker, None)

    def reset_connection(self) -> None:
        self.seq.reset()
        self.sids = {}
        for b in self.books.values():
            b.invalidate()

    def book(self, ticker: str) -> OrderBook:
        b = self.books.get(ticker)
        if b is None:
            m = self.meta.get(ticker) or MarketMeta()
            b = self.books[ticker] = OrderBook(VENUE, ticker, m.outcome, m.pair)
        return b

    def _invalidate_sid(self, sid) -> None:
        for tk, s in self._sid_of.items():
            if s == sid and tk in self.books:
                self.books[tk].invalidate()

    def process(self, msg: dict, recv_ns: int) -> Events:
        ev = Events()
        typ = msg.get("type")
        body = msg.get("msg") or {}
        if typ == "subscribed":
            if body.get("channel") is not None and body.get("sid") is not None:
                self.sids[body["channel"]] = body["sid"]
            return ev
        if typ == "ok":
            # update_subscription replies carry the subscription's seq (seen live 2026-10-13):
            # consume it so the next delta is not mistaken for a gap.
            sid, seq = msg.get("sid"), msg.get("seq")
            if seq is not None and sid is not None and sid == self.sids.get("orderbook_delta"):
                r = self.seq.check(sid, int(seq))
                if r.status == "gap":
                    log.warning("kalshi seq gap (ok reply) sid=%s expected=%s got=%s", sid, r.expected, r.got)
                    self._invalidate_sid(sid)
                    ev.resync = True
            return ev
        if typ in ("orderbook_snapshot", "orderbook_delta"):
            sid, seq = msg.get("sid"), msg.get("seq")
            if seq is not None:
                r = self.seq.check(sid, int(seq))
                if r.status == "stale":
                    return ev
                if r.status == "gap":
                    log.warning("kalshi seq gap sid=%s expected=%s got=%s", sid, r.expected, r.got)
                    self._invalidate_sid(sid)
                    ev.resync = True
            tk = body.get("market_ticker")
            if not tk or (self.active is not None and tk not in self.active):
                return ev
            b = self.book(tk)
            if typ == "orderbook_snapshot":
                bids, asks = to_yes_terms(_levels(body, "yes"), _levels(body, "no"))
                b.apply_snapshot(
                    bids, asks, seq=seq, ts_recv_ns=recv_ns,
                    ts_exchange_ns=ms_to_ns(msg.get("sending_ts_ms")),
                )
                self._sid_of[tk] = sid
                ev.book_updates.append(tk)
                return ev
            # delta
            if ev.resync or not b.valid:
                ev.resync = True
                ev.resync_ids.add(tk)
                return ev
            if body.get("price_dollars") not in (None, ""):
                px = float(body["price_dollars"])
            else:
                px = fnum(body.get("price")) / 100.0
            delta = fnum(body.get("delta_fp", body.get("delta")))
            side = body.get("side")
            ts = ms_to_ns(body.get("ts_ms")) or rfc3339_to_ns(body.get("ts")) or ms_to_ns(msg.get("sending_ts_ms"))
            stamp = dict(seq=seq, ts_exchange_ns=ts, ts_recv_ns=recv_ns)
            if side == "yes":
                ok = b.apply_delta("bid", px, delta, **stamp)
            elif side == "no":
                ok = b.apply_delta("ask", norm_px(1.0 - px), delta, **stamp)
            else:
                return ev
            if not ok:
                log.warning("kalshi negative level %s %s %s", tk, side, px)
                b.gap_since_sample = True
                ev.resync = True
                ev.resync_ids.add(tk)
            ev.book_updates.append(tk)
        elif typ == "trade":
            tk = body.get("market_ticker")
            if self.active is not None and tk not in self.active:
                return ev
            ts = ms_to_ns(body.get("ts_ms"))
            if ts is None and body.get("ts") is not None:
                ts = int(body["ts"]) * 1_000_000_000
            ev.trades.append(
                trade_row(
                    VENUE, tk, self.meta.get(tk),
                    trade_id=str(body.get("trade_id")),
                    ts_exchange_ns=ts, ts_recv_ns=recv_ns,
                    price=_trade_price(body), size=_trade_size(body),
                    taker_side=body.get("taker_side"), source="ws",
                )
            )
        elif typ == "error":
            log.error("kalshi ws error: %s", body)
            self.errors.append(body)
        return ev


# ---------------------------------------------------------------------------
# Auth (only needed for WS)
# ---------------------------------------------------------------------------


AUDIT_LEVELS = 3  # top levels per side compared against REST
AUDIT_RECHECK_S = 600.0  # do not audit the same market again within this window
AUDIT_RECONNECT_MIN_S = 600.0  # at most one audit-triggered reconnect per shard per window


def audit_candidates(books: Dict[str, OrderBook], now: int, silence_ns: int,
                     last_audit: Dict[str, int], limit: int, recheck_ns: int) -> List[str]:
    """Silence audit: valid books with no venue message for >= ``silence_ns`` that were not
    audited within ``recheck_ns``; least recently audited (then longest silent) first."""
    cand = []
    for tk, b in books.items():
        if not b.valid or b.ts_recv_ns is None or now - b.ts_recv_ns < silence_ns:
            continue
        la = last_audit.get(tk)
        if la is not None and now - la < recheck_ns:
            continue
        cand.append((la or 0, b.ts_recv_ns, tk))
    cand.sort()
    return [tk for _, _, tk in cand[:limit]]


def book_matches_rest(book: OrderBook, bids: List[Level], asks: List[Level], n: int = AUDIT_LEVELS) -> bool:
    """Do the top ``n`` levels (price and size) of our WS book equal a REST snapshot (YES terms)?"""
    ref = OrderBook(VENUE, book.market_id)
    ref.apply_snapshot(bids, asks)
    return book.top(n) == ref.top(n)


def ws_credentials() -> Optional[Tuple[str, str]]:
    kid = os.environ.get("KALSHI_API_KEY_ID")
    path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if kid and path and os.path.exists(path):
        return kid, path
    return None


def ws_auth_headers(key_id: str, key_path: str) -> Dict[str, str]:
    """Signature = base64(sign(timestamp_ms + 'GET' + '/trade-api/ws/v2')). RSA-PSS/SHA256 or Ed25519.

    Requires the optional ``cryptography`` package. Signing is ``predict_ngin.venues.kalshi.auth``;
    the key file is read on every call, as before.
    """
    from predict_ngin.venues.kalshi.auth import load_private_key, signed_headers

    key = load_private_key(key_path)
    try:
        return signed_headers(key, key_id, "GET", WS_SIGN_PATH)
    except ValueError:
        raise ValueError("unsupported Kalshi key type") from None


def ws_available() -> bool:
    if ws_credentials() is None:
        return False
    try:
        import cryptography  # noqa: F401
    except ImportError:
        log.warning("KALSHI_* env vars set but 'cryptography' not installed -> REST polling")
        return False
    return True


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


async def _get(client, bucket: TokenBucket, url: str, params=None) -> Optional[dict]:
    await bucket.acquire()
    r = await client.get(url, params=params)
    if r.status_code == 429:
        raise RuntimeError("rate limited (429)")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


async def run_rest(ctx: FeedContext, client) -> None:
    vc = ctx.venue_cfg
    bucket = TokenBucket(vc.max_requests_per_s, burst=vc.max_requests_per_s)
    tickers = list(ctx.current_markets())
    need = len(tickers) / max(vc.rest_poll_interval_s, 1e-6)
    if need > vc.max_requests_per_s:
        log.warning("kalshi: %d markets every %.1fs needs %.1f req/s > cap %.1f; polls will stretch",
                    len(tickers), vc.rest_poll_interval_s, need, vc.max_requests_per_s)
    books = {t: ctx.new_book(t) for t in tickers}
    last_ok = {t: None for t in tickers}
    backoff = Backoff()
    trade_task = asyncio.create_task(_poll_trades(ctx, client, bucket))
    try:
        while not ctx.stop.is_set():
            cycle_start = asyncio.get_running_loop().time()
            tickers = list(ctx.current_markets())
            for t in tickers:
                if t not in books:
                    books[t], last_ok[t] = ctx.new_book(t), None
            for t in [t for t in books if t not in set(tickers)]:
                books.pop(t, None), last_ok.pop(t, None), ctx.forget(t)
            for t in tickers:
                if ctx.stop.is_set():
                    break
                try:
                    data = await _get(client, bucket, f"{API}/markets/{t}/orderbook", {"depth": ctx.depth})
                    backoff.reset()
                except Exception as e:
                    ctx.stats["errors"] += 1
                    log.warning("kalshi rest %s: %s", t, e)
                    if await ctx.sleep(backoff.next()):
                        break
                    continue
                if data is None:
                    ctx.stats["not_found"] += 1
                    continue
                recv = now_ns()
                bids, asks = parse_rest_orderbook(data)
                b = books[t]
                prev = last_ok[t]
                if prev is not None and recv - prev > 3 * vc.rest_poll_interval_s * 1e9:
                    b.gap_since_sample = True  # polling stalled -> we may have missed states
                b.apply_snapshot(bids, asks, ts_recv_ns=recv)
                last_ok[t] = recv
                ctx.stats["polls"] += 1
                ctx.emit_due({t: b}, recv, "rest")
            elapsed = asyncio.get_running_loop().time() - cycle_start
            if await ctx.sleep(vc.rest_poll_interval_s - elapsed):
                break
    finally:
        trade_task.cancel()
        await asyncio.gather(trade_task, return_exceptions=True)


async def _poll_trades(ctx: FeedContext, client, bucket: TokenBucket) -> None:
    vc = ctx.venue_cfg
    if vc.trades_poll_interval_s <= 0:
        return
    meta = ctx.meta()
    seen: Dict[str, dict] = {}  # ticker -> ordered set of trade ids
    since: Dict[str, int] = {}
    while not ctx.stop.is_set():
        cur = ctx.current_markets()
        for t in cur:
            if t not in seen:
                seen[t], since[t] = {}, int(now_ns() // 1_000_000_000) - 5
        for t in [t for t in seen if t not in cur]:
            seen.pop(t), since.pop(t)
        for t in list(seen):
            try:
                data = await _get(client, bucket, f"{API}/markets/trades",
                                  {"ticker": t, "min_ts": since[t], "limit": 1000})
            except Exception as e:
                ctx.stats["errors"] += 1
                log.debug("kalshi trades %s: %s", t, e)
                continue
            rows = [r for r in parse_rest_trades(data or {}, now_ns(), meta) if r["trade_id"] not in seen[t]]
            for r in rows:
                seen[t][r["trade_id"]] = None
                if r["ts_exchange_ns"]:
                    since[t] = max(since[t], r["ts_exchange_ns"] // 1_000_000_000 - 1)
            if len(seen[t]) > 5000:
                seen[t] = dict.fromkeys(list(seen[t])[-1000:])
            ctx.emit_trades(rows)
        if await ctx.sleep(vc.trades_poll_interval_s):
            return


CHANNELS = ["orderbook_delta", "trade"]
_CHUNK = 500  # tickers per subscribe/update command


class KalshiShard:
    """One authenticated WS connection holding a set of markets that can grow/shrink live
    (``update_subscription`` add_markets / delete_markets on both channel sids)."""

    def __init__(self, ctx: FeedContext, idx: int, key_id: str, key_path: str):
        self.ctx, self.idx = ctx, idx
        self.key_id, self.key_path = key_id, key_path
        self.want: set = set()  # tickers this shard should hold
        self.subbed: set = set()  # tickers sent to the server on the current connection
        self.state = KalshiWsState(ctx.meta(), active=self.want)
        self.kick = asyncio.Event()
        self._id = 0
        self.connected = False
        self.force_reconnect = False  # set by the silence audit
        self.last_forced_ns = 0

    def update(self, add: Iterable[str] = (), remove: Iterable[str] = ()) -> None:
        for t in remove:
            self.want.discard(t)
            self.state.drop(t)
        self.want.update(add)
        self.kick.set()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _sync(self, ws) -> None:
        """Bring the server-side subscription in line with ``want``."""
        if not self.state.sids:
            if not self.subbed and self.want:
                # never subscribe with an empty ticker list (that would mean "all markets")
                tks = sorted(self.want)
                await ws.send(json.dumps({"id": self._next_id(), "cmd": "subscribe", "params": {
                    "channels": CHANNELS, "market_tickers": tks[:_CHUNK]}}))
                self.subbed = set(tks[:_CHUNK])
            return  # wait for the "subscribed" replies (sids) before updating
        if len(self.state.sids) < len(CHANNELS):
            return
        add = sorted(self.want - self.subbed)
        rem = sorted(self.subbed - self.want)
        for action, tks in (("delete_markets", rem), ("add_markets", add)):
            for i in range(0, len(tks), _CHUNK):
                chunk = tks[i:i + _CHUNK]
                for ch in CHANNELS:
                    await ws.send(json.dumps({"id": self._next_id(), "cmd": "update_subscription", "params": {
                        "sids": [self.state.sids[ch]], "market_tickers": chunk, "action": action}}))
                self.ctx.stats["sub_" + action] += len(chunk)
        self.subbed = set(self.want)

    async def run(self) -> None:
        import websockets

        ctx, state = self.ctx, self.state
        backoff = Backoff()
        while not ctx.stop.is_set():
            if not self.want:
                self.kick.clear()
                try:
                    await asyncio.wait_for(self.kick.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                headers = ws_auth_headers(self.key_id, self.key_path)
                async with websockets.connect(WS_URL, additional_headers=headers, ping_interval=10,
                                              max_size=2 ** 24, max_queue=MAX_QUEUE) as ws:
                    state.reset_connection()
                    self.subbed = set()
                    self.force_reconnect = False
                    self.connected = True
                    ctx.stats["connects"] += 1
                    log.info("kalshi ws[%d] connected, %d markets", self.idx, len(self.want))
                    while not ctx.stop.is_set():
                        if self.want != self.subbed:
                            self.kick.clear()
                            await self._sync(ws)
                        if self.force_reconnect:
                            log.warning("kalshi ws[%d]: silence audit found a stale book -> reconnecting", self.idx)
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        ev = state.process(json.loads(raw), now_ns())
                        ctx.stats["msgs"] += 1
                        ctx.emit_trades(ev.trades)
                        if state.errors:
                            for e in state.errors:
                                ctx.stats["ws_errors"] += 1
                                if e.get("code") == 26:
                                    log.error("kalshi ws[%d]: per-subscription market limit hit at %d markets; "
                                              "lower venues.kalshi.max_markets_per_conn", self.idx, len(self.subbed))
                            state.errors.clear()
                        if ev.resync:
                            ctx.stats["resyncs"] += 1
                            log.warning("kalshi ws[%d]: resync -> reconnecting for fresh snapshots", self.idx)
                            break
                        backoff.reset()
            except Exception as e:
                ctx.stats["errors"] += 1
                log.warning("kalshi ws[%d] error: %s", self.idx, e)
            self.connected = False
            for b in state.books.values():
                b.invalidate()
            if ctx.stop.is_set():
                break
            if await ctx.sleep(backoff.next()):
                break


async def _audit_one(ctx: FeedContext, client, bucket: TokenBucket, sh: "KalshiShard", tk: str) -> Optional[bool]:
    """REST-check one silent book. True = matches (book confirmed current), False = differs on
    two REST reads 3 s apart while no WS message arrived (the subscription stopped
    delivering), None = inconclusive (book changed meanwhile, market gone, REST failed)."""
    b = sh.state.books.get(tk)
    if b is None or not b.valid:
        return None
    for attempt in range(2):
        v = b.version
        data = await _get(client, bucket, f"{API}/markets/{tk}/orderbook", {"depth": AUDIT_LEVELS})
        if data is None or b.version != v or not b.valid or sh.state.books.get(tk) is not b:
            return None
        bids, asks = parse_rest_orderbook(data)
        if book_matches_rest(b, bids, asks):
            b.ts_recv_ns = now_ns()  # the venue confirmed the book: current, not stale
            return True
        if attempt == 0 and await ctx.sleep(3.0):  # a delta may be in flight; re-read once
            return None
    log.warning("kalshi audit %s: WS book silent %.0fs and differs from REST (ours %s/%s)",
                tk, (now_ns() - (b.ts_recv_ns or 0)) / 1e9, b.best_bid, b.best_ask)
    return False


async def _audit_loop(ctx: FeedContext, client, shards: Dict[int, "KalshiShard"]) -> None:
    """Every ``audit_interval_s``, REST-check up to ``audit_max_per_round`` books that had no WS
    message for ``silence_audit_s``. Kalshi only sends deltas on change, so silence alone is
    normal for quiet markets; a REST mismatch is not. A confirmed mismatch invalidates the
    book and reconnects its shard (fresh snapshots), at most once per shard per 10 min."""
    vc = ctx.venue_cfg
    silence_ns = int(float(getattr(vc, "silence_audit_s", 0) or 0) * 1e9)
    if silence_ns <= 0 or client is None:
        return
    bucket = TokenBucket(1.0, burst=1.0)  # shares the IP with discovery; keep it light
    last_audit: Dict[str, int] = {}
    interval = max(5.0, float(getattr(vc, "audit_interval_s", 60.0)))
    limit = max(1, int(getattr(vc, "audit_max_per_round", 10)))
    recheck_ns = int(AUDIT_RECHECK_S * 1e9)
    while not ctx.stop.is_set():
        if await ctx.sleep(interval):
            return
        t = now_ns()
        live = {tk for sh in list(shards.values()) for tk in sh.state.books}
        for tk in [k for k in last_audit if k not in live]:
            del last_audit[tk]
        todo = []
        for sh in list(shards.values()):
            if sh.connected and not sh.force_reconnect:
                todo += [(sh, tk) for tk in audit_candidates(sh.state.books, t, silence_ns, last_audit,
                                                             limit, recheck_ns)]
        todo.sort(key=lambda x: last_audit.get(x[1], 0))
        for sh, tk in todo[:limit]:
            if ctx.stop.is_set():
                return
            last_audit[tk] = now_ns()
            try:
                r = await _audit_one(ctx, client, bucket, sh, tk)
            except Exception as e:
                ctx.stats["audit_errors"] += 1
                log.debug("kalshi audit %s: %s", tk, e)
                continue
            if r is None:
                ctx.stats["audit_skipped"] += 1
            elif r:
                ctx.stats["audit_ok"] += 1
            else:
                ctx.stats["audit_stale"] += 1
                b = sh.state.books.get(tk)
                if b is not None:
                    b.invalidate()  # stop writing rows from a book known to be wrong
                if now_ns() - sh.last_forced_ns >= AUDIT_RECONNECT_MIN_S * 1e9:
                    sh.last_forced_ns = now_ns()
                    sh.force_reconnect = True
                    ctx.stats["audit_reconnects"] += 1


async def run_ws(ctx: FeedContext, client=None) -> None:
    """Static markets (config pairs/markets) on connection 0; discovered markets on
    connections 1..n with at most ``max_markets_per_conn`` each. Market sets change live."""
    key_id, key_path = ws_credentials()
    cap = max(1, int(getattr(ctx.venue_cfg, "max_markets_per_conn", 1000)))
    shards: Dict[int, KalshiShard] = {}
    tasks: Dict[int, asyncio.Task] = {}
    assigned: Dict[str, int] = {}
    sample_dt = ctx.sampler.interval_ns / 1e9 or 1.0
    universe = ctx.universe
    static_ids = set(universe.current(VENUE, "static")) if universe is not None else {m.market_id for m in ctx.markets}

    def shard(i: int) -> KalshiShard:
        if i not in shards:
            shards[i] = KalshiShard(ctx, i, key_id, key_path)
            tasks[i] = asyncio.create_task(shards[i].run(), name=f"kalshi-ws-{i}")
        return shards[i]

    def reconcile() -> None:
        from ..universe import assign_shards

        cur = ctx.current_markets()
        static = [t for t in cur if t in static_ids]
        dyn = [t for t in cur if t not in static_ids]
        if static:
            s0 = shard(0)
            add0 = [t for t in static if t not in s0.want]
            rem0 = [t for t in s0.want if t not in cur]
            if add0 or rem0:
                s0.update(add0, rem0)
        dyn_assigned = {t: s - 1 for t, s in assigned.items() if s > 0}
        new, removed, add_by, rem_by = assign_shards(dyn, dyn_assigned, {}, cap)
        for s, tks in rem_by.items():
            shard(s + 1).update(remove=tks)
        for t in removed:
            ctx.forget(t)
        for s, tks in add_by.items():
            shard(s + 1).update(add=tks)
        assigned.clear()
        assigned.update({t: 0 for t in static})
        assigned.update({t: s + 1 for t, s in new.items()})
        ctx.stats["markets"] = len(assigned)
        ctx.stats["connections"] = len(shards)

    async def sampler_loop():
        while not ctx.stop.is_set():
            t = now_ns()
            for sh in list(shards.values()):
                ctx.emit_due(sh.state.books, t, "ws")
            if await ctx.sleep(sample_dt):
                return

    sampler_task = asyncio.create_task(sampler_loop())
    audit_task = asyncio.create_task(_audit_loop(ctx, client, shards))
    seen_version = -1
    try:
        while not ctx.stop.is_set():
            v = universe.version if universe is not None else 0
            if v != seen_version:
                seen_version = v
                reconcile()
            if await ctx.sleep(1.0):
                break
    finally:
        sampler_task.cancel()
        audit_task.cancel()
        for t in tasks.values():
            t.cancel()
        await asyncio.gather(sampler_task, audit_task, *tasks.values(), return_exceptions=True)


async def run(ctx: FeedContext, client) -> None:
    mode = ctx.venue_cfg.mode
    if mode in ("ws", "auto") and ws_available():
        await run_ws(ctx, client)
        return
    if mode == "ws":
        log.warning("kalshi: ws requested but no usable API key (KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY_PATH) "
                    "-> falling back to REST polling")
    await run_rest(ctx, client)
