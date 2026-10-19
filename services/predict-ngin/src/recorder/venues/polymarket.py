"""Polymarket (global CLOB) adapter -- read-only, no keys.

Verified 2026-10-12 against https://docs.polymarket.com and live endpoints:

* REST ``GET https://clob.polymarket.com/book?token_id=..`` and batch ``POST /books``
  (body ``[{"token_id": ..}, ..]``). Public. ``bids`` ascending / ``asks`` descending
  (best level last), ``timestamp`` in ms, ``hash``.
* WS ``wss://ws-subscriptions-clob.polymarket.com/ws/market`` -- public. Subscribe
  ``{"assets_ids": [...], "type": "market"}``; client must send text ``PING`` every 10 s.
  Events: ``book`` (full snapshot), ``price_change`` (``price_changes[]`` with absolute
  ``size`` per level plus ``best_bid``/``best_ask``), ``last_trade_price``, ``tick_size_change``.

  With ``"custom_feature_enabled": true`` the venue also sends ``best_bid_ask`` (top of
  book on every change) -- recorded verbatim to the ``bbo`` table so downstream can
  trust the BBO even if depth reconstruction drifts.

There are no sequence numbers on this feed. Integrity is checked by comparing our
reconstructed best bid/ask with the ``best_bid``/``best_ask`` echoed on price_change.

IMPORTANT (root-caused 2026-10-12 from a live capture, see
tests/unit/fixtures/recorder_polymarket_split_update.jsonl): the venue splits ONE
matching-engine update into several price_change messages sharing the same
``timestamp`` (e.g. msg 1: BUY 0.65 size 15; msg 2: SELL 0.65 size 0), and the
best_bid/best_ask on EVERY message of the group already reflect the state after the
whole group. Checking per message flagged the intermediate (often crossed) state as a
mismatch and forced needless REST resyncs. The check is therefore deferred until the
group is closed: a later price_change for that asset with a different timestamp, or
the group being older than ``GROUP_SETTLE_NS`` (see ``verify_pending``). Books with
an open group are not sampled.

REST snapshots older than the WS state already applied are ignored (a periodic
resync must not roll the book back).

Each token id (YES and NO outcome) is its own book, recorded in that token's price terms.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, List, Optional, Tuple

from ..book import OrderBook
from ..core import Events, FeedContext, MarketMeta, bbo_row, trade_row
from ..util import Backoff, TokenBucket, fnum, ms_to_ns, now_ns

log = logging.getLogger("recorder.polymarket")

VENUE = "polymarket"
CLOB = "https://clob.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA = "https://gamma-api.polymarket.com"

Level = Tuple[float, float]

# Frames buffered client-side before websockets stops reading the socket. The default (16)
# made Polymarket drop the connection ('1013 slow consumer') during bursts of the crypto
# up/down books (~1,300 msgs/s) on 2026-10-13.
MAX_QUEUE = 16384

# An open price_change group (same asset + timestamp) is judged once it is this old.
GROUP_SETTLE_NS = 500_000_000


def parse_book(payload: dict) -> Tuple[List[Level], List[Level], Optional[int]]:
    bids = [(float(l["price"]), float(l["size"])) for l in payload.get("bids") or []]
    asks = [(float(l["price"]), float(l["size"])) for l in payload.get("asks") or []]
    return bids, asks, ms_to_ns(payload.get("timestamp"))


def _opt_px(v) -> Optional[float]:
    if v in (None, ""):
        return None
    f = float(v)
    return None if f == 0 else f


class PolymarketWsState:
    def __init__(self, meta: Optional[Dict[str, MarketMeta]] = None, active: Optional[set] = None):
        self.meta = meta if meta is not None else {}
        self.active = active  # if set, `book` snapshots for other tokens are ignored
        self.books: Dict[str, OrderBook] = {}
        self.bbo_mismatches = 0
        self.bbo_checks = 0
        self.stale_rest_skipped = 0
        # asset -> (group timestamp, venue best_bid, venue best_ask, first recv_ns)
        self._pending: Dict[str, Tuple[object, Optional[float], Optional[float], int]] = {}

    # -- deferred best bid/ask verification ---------------------------------

    def _check(self, asset: str, ev: Optional[Events] = None) -> bool:
        _, eb, ea, _ = self._pending.pop(asset)
        b = self.books.get(asset)
        if b is None or not b.valid:
            return True
        self.bbo_checks += 1
        if ea is not None and ea >= 1.0:
            ea = None  # venue echoes best_ask "1" for an empty ask side (1.0 is not an order price)
        if _close(b.best_bid, eb) and _close(b.best_ask, ea):
            return True
        self.bbo_mismatches += 1
        log.warning("polymarket bbo mismatch %s: ours %s/%s venue %s/%s",
                    asset[:12], b.best_bid, b.best_ask, eb, ea)
        b.invalidate()
        if ev is not None:
            ev.resync = True
            ev.resync_ids.add(asset)
        return False

    def verify_pending(self, now_ns: int, settle_ns: int = GROUP_SETTLE_NS) -> int:
        """Judge every open price_change group whose first message is >= ``settle_ns`` old.
        Returns the number of mismatches found."""
        bad = 0
        for asset, (_, _, _, t0) in list(self._pending.items()):
            if now_ns - t0 >= settle_ns and not self._check(asset):
                bad += 1
        return bad

    def sampleable_books(self, now_ns: int) -> List[OrderBook]:
        """Valid books that are not mid-update: either no open price_change group, or the
        open group's end state is already reached (our best bid/ask equal the venue's echo,
        which always describes the state after the whole group). On very active books
        (crypto up/down, ~100 msgs/s per token) a group is open almost all the time, so
        requiring "no open group" starved sampling (2026-10-13)."""
        out = []
        for a, b in self.books.items():
            if not b.valid:
                continue
            pend = self._pending.get(a)
            if pend is not None:
                _, eb, ea, _ = pend
                if ea is not None and ea >= 1.0:
                    ea = None
                if not (_close(b.best_bid, eb) and _close(b.best_ask, ea)):
                    continue
            out.append(b)
        return out

    def drop(self, asset: str) -> None:
        self.books.pop(asset, None)
        self._pending.pop(asset, None)

    def book(self, asset: str) -> OrderBook:
        b = self.books.get(asset)
        if b is None:
            m = self.meta.get(asset) or MarketMeta()
            b = self.books[asset] = OrderBook(VENUE, asset, m.outcome, m.pair)
        return b

    def apply_rest(self, payload: dict, recv_ns: int) -> Optional[str]:
        asset = str(payload.get("asset_id") or "")
        if not asset:
            return None
        bids, asks, ts = parse_book(payload)
        b = self.book(asset)
        if b.valid and ts is not None and b.ts_exchange_ns is not None and ts < b.ts_exchange_ns:
            self.stale_rest_skipped += 1  # WS state is already newer; don't roll back
            return None
        if not b.valid:
            b.gap_since_sample = True
        b.apply_snapshot(bids, asks, ts_exchange_ns=ts, ts_recv_ns=recv_ns)
        self._pending.pop(asset, None)
        return asset

    def process_raw(self, raw: str, recv_ns: int) -> Events:
        if not raw or raw in ("PONG", "PING"):
            return Events()
        try:
            msg = json.loads(raw)
        except ValueError:
            return Events()
        return self.process(msg, recv_ns)

    def process(self, msg, recv_ns: int) -> Events:
        ev = Events()
        for m in msg if isinstance(msg, list) else [msg]:
            if isinstance(m, dict):
                self._one(m, recv_ns, ev)
        return ev

    def _one(self, m: dict, recv_ns: int, ev: Events) -> None:
        et = m.get("event_type")
        ts = ms_to_ns(m.get("timestamp"))
        if et == "book":
            asset = str(m.get("asset_id"))
            if self.active is not None and asset not in self.active:
                return  # unsubscribed token (late message)
            bids, asks, ts = parse_book(m)
            self.book(asset).apply_snapshot(bids, asks, ts_exchange_ns=ts, ts_recv_ns=recv_ns)
            self._pending.pop(asset, None)  # full snapshot supersedes any open group
            ev.book_updates.append(asset)
        elif et == "price_change":
            changes = m.get("price_changes")
            group = m.get("timestamp")
            if changes is None:  # legacy format: top-level asset_id + changes[]
                changes = [dict(c, asset_id=m.get("asset_id")) for c in m.get("changes") or []]
            for c in changes:
                asset = str(c.get("asset_id"))
                pend = self._pending.get(asset)
                if pend is not None and pend[0] != group:
                    self._check(asset, ev)  # previous group is closed -> judge it now
                    pend = None
                b = self.books.get(asset)
                if b is None or not b.valid:
                    ev.resync_ids.add(asset)
                    continue
                side = "bid" if str(c.get("side", "")).upper() == "BUY" else "ask"
                b.set_level(side, float(c["price"]), fnum(c.get("size")), ts_exchange_ns=ts, ts_recv_ns=recv_ns)
                if "best_bid" in c or "best_ask" in c:
                    eb, ea = _opt_px(c.get("best_bid")), _opt_px(c.get("best_ask"))
                    t0 = pend[3] if pend is not None else recv_ns
                    self._pending[asset] = (group, eb, ea, t0)
                if asset not in ev.book_updates:
                    ev.book_updates.append(asset)
        elif et == "best_bid_ask":
            asset = str(m.get("asset_id"))
            ev.bbo.append(
                bbo_row(
                    VENUE, asset, self.meta.get(asset),
                    ts_exchange_ns=ts, ts_recv_ns=recv_ns,
                    best_bid=_opt_px(m.get("best_bid")), best_ask=_opt_px(m.get("best_ask")),
                    source="ws",
                )
            )
        elif et == "last_trade_price":
            asset = str(m.get("asset_id"))
            side = m.get("side")
            ev.trades.append(
                trade_row(
                    VENUE, asset, self.meta.get(asset),
                    trade_id=str(m.get("transaction_hash") or f"{asset}:{m.get('timestamp')}"),
                    ts_exchange_ns=ts, ts_recv_ns=recv_ns,
                    price=fnum(m.get("price")), size=fnum(m.get("size")),
                    taker_side=side.lower() if isinstance(side, str) else None,
                    source="ws",
                )
            )
        elif et == "tick_size_change":
            log.info("polymarket tick size change %s: %s -> %s", m.get("asset_id"),
                     m.get("old_tick_size"), m.get("new_tick_size"))


def _close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < 1e-9


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


async def fetch_books(client, bucket: TokenBucket, tokens: List[str]) -> List[dict]:
    out: List[dict] = []
    for i in range(0, len(tokens), 50):
        await bucket.acquire()
        r = await client.post(f"{CLOB}/books", json=[{"token_id": t} for t in tokens[i:i + 50]])
        r.raise_for_status()
        out.extend(r.json() or [])
    return out


async def _rest_snapshot(ctx: FeedContext, client, bucket, state: PolymarketWsState, tokens) -> int:
    books = await fetch_books(client, bucket, tokens)
    recv = now_ns()
    n = 0
    for p in books:
        if state.apply_rest(p, recv):
            n += 1
    ctx.stats["rest_snapshots"] += n
    return n


async def run_rest(ctx: FeedContext, client) -> None:
    vc = ctx.venue_cfg
    bucket = TokenBucket(vc.max_requests_per_s)
    state = PolymarketWsState(ctx.meta())
    tokens = [m.market_id for m in ctx.markets]
    backoff = Backoff()
    while not ctx.stop.is_set():
        try:
            await _rest_snapshot(ctx, client, bucket, state, tokens)
            ctx.stats["polls"] += 1
            backoff.reset()
            ctx.emit_due(state.books, now_ns(), "rest")
            delay = vc.rest_poll_interval_s
        except Exception as e:
            ctx.stats["errors"] += 1
            log.warning("polymarket rest: %s", e)
            delay = backoff.next()
        if await ctx.sleep(delay):
            break


DEAD_RETRY_S = 1800.0  # a token REST no longer returns (resolved market) is retried this rarely


async def run_ws(ctx: FeedContext, client) -> None:
    """Market WS. The token set follows ``ctx.current_markets()``: new tokens are added with
    ``{"operation": "subscribe"}`` and rolled-out ones removed with ``"unsubscribe"`` on the
    open connection (verified live 2026-10-13), so rollover never drops the other books."""
    import websockets

    vc = ctx.venue_cfg
    bucket = TokenBucket(vc.max_requests_per_s)
    active: set = set()
    state = PolymarketWsState(ctx.meta(), active=active)
    backoff = Backoff()
    sample_dt = ctx.sampler.interval_ns / 1e9 or 1.0
    dead: Dict[str, float] = {}  # token -> monotonic time it was last found missing on REST

    def refresh_active() -> Tuple[List[str], List[str]]:
        cur = set(ctx.current_markets())
        add = sorted(cur - active)
        rem = sorted(active - cur)
        for t in rem:
            active.discard(t)
            state.drop(t)
            dead.pop(t, None)
            ctx.forget(t)
        active.update(add)
        return add, rem

    async def sampler_loop():
        while not ctx.stop.is_set():
            t = now_ns()
            state.verify_pending(t)
            ctx.emit_due(state.sampleable_books(t), t, "ws")
            ctx.stats["bbo_checks"] = state.bbo_checks
            ctx.stats["bbo_mismatches"] = state.bbo_mismatches
            ctx.stats["stale_rest_skipped"] = state.stale_rest_skipped
            ctx.stats["tokens"] = len(active)
            ctx.stats["dead_tokens"] = len(dead)
            if await ctx.sleep(min(sample_dt, 0.5)):
                return

    async def resync_loop():
        """Periodic REST snapshot + immediate snapshot for invalidated books."""
        last_full = asyncio.get_running_loop().time()
        while not ctx.stop.is_set():
            if await ctx.sleep(2.0):
                return
            now = asyncio.get_running_loop().time()
            live = [t for t in active if now - dead.get(t, -1e18) >= DEAD_RETRY_S]
            bad = [t for t in live if t in state.books and not state.books[t].valid]
            full = vc.resync_interval_s > 0 and now - last_full >= vc.resync_interval_s
            todo = live if full else bad
            if not todo:
                if full:
                    last_full = now
                continue
            try:
                got = {str(p.get("asset_id")) for p in await _rest_snapshot_ids(ctx, client, bucket, state, todo)}
                for t in todo:
                    if t in got:
                        dead.pop(t, None)
                    else:
                        dead[t] = now  # no book on REST: market resolved/closed
                ctx.stats["resyncs"] += len([t for t in bad if t in got])
                if full:
                    last_full = now
            except Exception as e:
                ctx.stats["errors"] += 1
                log.warning("polymarket resync: %s", e)

    tasks = [asyncio.create_task(sampler_loop()), asyncio.create_task(resync_loop())]
    try:
        while not ctx.stop.is_set():
            refresh_active()
            if not active:
                if await ctx.sleep(2.0):
                    break
                continue
            try:
                async with websockets.connect(WS_URL, ping_interval=None, max_size=2 ** 23,
                                              max_queue=MAX_QUEUE) as ws:
                    await ws.send(json.dumps({"assets_ids": sorted(active), "type": "market",
                                              "custom_feature_enabled": True}))
                    ctx.stats["connects"] += 1
                    log.info("polymarket ws[%s] connected, %d tokens", ctx.universe_kind, len(active))
                    loop = asyncio.get_running_loop()
                    last_ping = last_check = loop.time()
                    seen_version = getattr(ctx.universe, "version", 0)
                    while not ctx.stop.is_set():
                        now = loop.time()
                        if now - last_ping >= 10:
                            await ws.send("PING")
                            last_ping = now
                        v = getattr(ctx.universe, "version", 0)
                        if v != seen_version and now - last_check >= 1.0:
                            seen_version, last_check = v, now
                            add, rem = refresh_active()
                            if rem:
                                await ws.send(json.dumps({"assets_ids": rem, "operation": "unsubscribe"}))
                            if add:
                                await ws.send(json.dumps({"assets_ids": add, "operation": "subscribe",
                                                          "custom_feature_enabled": True}))
                            if add or rem:
                                ctx.stats["sub_add"] += len(add)
                                ctx.stats["sub_remove"] += len(rem)
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        ev = state.process_raw(raw, now_ns())
                        ctx.stats["msgs"] += 1
                        ctx.emit_trades([r for r in ev.trades if r["market_id"] in active])
                        ctx.emit_bbo([r for r in ev.bbo if r["market_id"] in active])
                        backoff.reset()
            except Exception as e:
                ctx.stats["errors"] += 1
                log.warning("polymarket ws[%s] error: %s", ctx.universe_kind, e)
            for b in state.books.values():
                b.invalidate()  # reconnect brings fresh `book` snapshots
            if ctx.stop.is_set():
                break
            if await ctx.sleep(backoff.next()):
                break
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _rest_snapshot_ids(ctx: FeedContext, client, bucket, state: "PolymarketWsState", tokens) -> List[dict]:
    books = await fetch_books(client, bucket, tokens)
    recv = now_ns()
    n = 0
    for p in books:
        if state.apply_rest(p, recv):
            n += 1
    ctx.stats["rest_snapshots"] += n
    return books


async def run(ctx: FeedContext, client) -> None:
    if ctx.venue_cfg.mode == "rest":
        await run_rest(ctx, client)
    else:
        await run_ws(ctx, client)
