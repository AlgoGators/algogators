"""Fake Kalshi exchange behind the HTTP transport (no network) for the LIP pilot tests."""
from __future__ import annotations

import copy
import email.utils
import itertools
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.live.lip_pilot.client import Response, Transport  # noqa: E402
from src.live.lip_pilot.config import LiveGate, PilotConfig, load_config  # noqa: E402
from src.live.lip_pilot.journal import Journal  # noqa: E402
from src.live.lip_pilot.ratelimit import TokenBucket  # noqa: E402
from src.live.lip_pilot.client import KalshiClient  # noqa: E402
from src.live.lip_pilot.runner import Pilot  # noqa: E402

T0 = 1_792_000_000.0  # fixed fake epoch (2026-10-15ish)


def iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeClock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeSigner:
    def headers(self, method, path):
        return {"KALSHI-ACCESS-KEY": "k", "KALSHI-ACCESS-TIMESTAMP": "0", "KALSHI-ACCESS-SIGNATURE": "s"}


def default_book() -> Dict[str, list]:
    # YES: BB 0.15 x30 -> BB1 0.14 (cheap side: 250 lots = $35). NO: BB 0.80 x20 -> BB1 0.79.
    return {"yes": [[0.15, 30], [0.14, 100], [0.10, 1200]],
            "no": [[0.80, 20], [0.79, 50], [0.70, 1500]]}


@dataclass
class FakeExchange(Transport):
    clock: FakeClock
    tickers: List[str] = field(default_factory=lambda: [f"KXAAAGASD{s}-26OCT16-3.{i}" for i, s in
                                                        enumerate(["OH", "IN", "IL", "WI", "CO", "FL", "NV", "MI"])])
    books: Dict[str, Dict[str, list]] = field(default_factory=dict)
    close_in_s: float = 6 * 3600
    skew_s: float = 0.0
    fail_paths: Dict[str, int] = field(default_factory=dict)   # path substring -> HTTP status
    network_fail_once: set = field(default_factory=set)
    entry_fill: float = 0.0                                    # simulate a taker fill on entry
    calls: List[tuple] = field(default_factory=list)
    resting: Dict[str, dict] = field(default_factory=dict)
    by_cid: Dict[str, str] = field(default_factory=dict)
    fills: List[dict] = field(default_factory=list)
    positions: Dict[str, float] = field(default_factory=dict)
    foreign: List[dict] = field(default_factory=list)

    def __post_init__(self):
        self._ids = itertools.count(1)
        for t in self.tickers:
            self.books.setdefault(t, default_book())

    # ------------------------------------------------------------------ helpers for tests
    def mutating_calls(self) -> List[tuple]:
        return [c for c in self.calls if c[0] != "GET"]

    def fill(self, order_id: str, count: float) -> None:
        o = self.resting[order_id]
        count = min(count, o["remaining"])
        o["remaining"] -= count
        o["filled"] += count
        outcome = o["outcome"]
        px = o["px"]
        self.fills.append({"fill_id": f"f{len(self.fills) + 1}", "trade_id": f"t{len(self.fills) + 1}",
                           "order_id": order_id, "ticker": o["ticker"], "market_ticker": o["ticker"],
                           "outcome_side": outcome, "count_fp": f"{count:.2f}",
                           "yes_price_dollars": f"{px if outcome == 'yes' else 1 - px:.4f}",
                           "no_price_dollars": f"{px if outcome == 'no' else 1 - px:.4f}",
                           "is_taker": False, "fee_cost": "0"})
        self.positions[o["ticker"]] = self.positions.get(o["ticker"], 0.0) + (count if outcome == "yes" else -count)
        if o["remaining"] <= 0:
            del self.resting[order_id]

    # ------------------------------------------------------------------ transport
    def request(self, method, url, *, params=None, json=None, headers=None, timeout=15.0) -> Response:
        path = url.split("/trade-api/v2", 1)[1]
        self.calls.append((method, path, copy.deepcopy(params), copy.deepcopy(json), bool(headers)))
        key = (method, path)
        if key in self.network_fail_once:
            self.network_fail_once.discard(key)
            raise ConnectionError("simulated network drop")
        hdr = {"Date": email.utils.formatdate(self.clock() - self.skew_s, usegmt=True)}
        for sub, status in self.fail_paths.items():
            if sub in path:
                return Response(status, {"error": "simulated"}, hdr)
        body = self._route(method, path, params or {}, json or {})
        if isinstance(body, Response):
            body.headers = hdr
            return body
        return Response(200 if method == "GET" else 201, body, hdr)

    def _route(self, method, path, params, body):
        now = self.clock()
        if method == "GET":
            if path == "/exchange/status":
                return {"trading_active": True, "exchange_active": True}
            if path == "/incentive_programs":
                return {"incentive_programs": [
                    {"id": f"p{i}", "market_id": t, "market_ticker": t, "incentive_type": "liquidity",
                     "start_date": iso(now - 3600), "end_date": iso(now + 12 * 3600),
                     "period_reward": 1_000_000, "paid_out": False, "discount_factor_bps": 5000,
                     "target_size_fp": "1000.00"} for i, t in enumerate(self.tickers)]}
            if path.endswith("/orderbook"):
                t = path.split("/")[2]
                bk = self.books[t]
                levels = {s: {} for s in ("yes", "no")}
                for s in ("yes", "no"):
                    for p, q in bk[s]:
                        levels[s][round(p, 4)] = levels[s].get(round(p, 4), 0) + q
                for o in self.resting.values():
                    if o["ticker"] == t:
                        k = round(o["px"], 4)
                        levels[o["outcome"]][k] = levels[o["outcome"]].get(k, 0) + o["remaining"]
                return {"orderbook_fp": {f"{s}_dollars": [[f"{p:.4f}", f"{q:.2f}"] for p, q in
                                                           sorted(levels[s].items())] for s in ("yes", "no")}}
            if path.startswith("/markets/"):
                t = path.split("/")[2]
                return {"market": {"ticker": t, "status": "active", "close_time": iso(now + self.close_in_s)}}
            if path == "/portfolio/orders":
                out = [self._order_view(oid, o) for oid, o in self.resting.items()] + list(self.foreign)
                if params.get("ticker"):
                    out = [o for o in out if o["ticker"] == params["ticker"]]
                return {"orders": out, "cursor": ""}
            if path == "/portfolio/positions":
                return {"market_positions": [{"ticker": t, "position_fp": f"{v:.2f}"}
                                             for t, v in self.positions.items()], "cursor": ""}
            if path == "/portfolio/fills":
                return {"fills": list(self.fills), "cursor": ""}
            if path == "/portfolio/balance":
                return {"balance": 100000}
            return Response(404, {"error": "not found"})
        if method == "POST" and path == "/portfolio/events/orders":
            cid = body["client_order_id"]
            if cid in self.by_cid:
                return Response(409, {"error": "ORDER_ALREADY_EXISTS"})
            oid = f"o{next(self._ids)}"
            outcome = "yes" if body["side"] == "bid" else "no"
            yes_px = float(body["price"])
            px = yes_px if outcome == "yes" else round(1 - yes_px, 4)
            count = float(body["count"])
            self.by_cid[cid] = oid
            fill = min(self.entry_fill, count)
            self.resting[oid] = {"ticker": body["ticker"], "outcome": outcome, "px": px, "count": count,
                                 "remaining": count - fill, "filled": fill, "cid": cid, "body": body}
            if self.resting[oid]["remaining"] <= 0:
                del self.resting[oid]
            return {"order_id": oid, "client_order_id": cid, "fill_count": f"{fill:.2f}",
                    "remaining_count": f"{count - fill:.2f}", "ts_ms": int(now * 1000)}
        if method == "DELETE" and path == "/portfolio/events/orders/batched":
            out = []
            for item in body["orders"]:
                o = self.resting.pop(item["order_id"], None)
                out.append({"order_id": item["order_id"], "reduced_by": f"{o['remaining'] if o else 0:.2f}",
                            "error": None if o else {"code": "not_found"}})
            return {"orders": out}
        if method == "DELETE" and path.startswith("/portfolio/events/orders/"):
            oid = path.rsplit("/", 1)[1]
            o = self.resting.pop(oid, None)
            if o is None:
                return Response(404, {"error": "not found"})
            return {"order_id": oid, "client_order_id": o["cid"], "reduced_by": f"{o['remaining']:.2f}"}
        return Response(404, {"error": "no route"})

    def _order_view(self, oid, o):
        return {"order_id": oid, "client_order_id": o["cid"], "ticker": o["ticker"], "outcome_side": o["outcome"],
                "status": "resting", "remaining_count_fp": f"{o['remaining']:.2f}",
                "fill_count_fp": f"{o['filled']:.2f}", "initial_count_fp": f"{o['count']:.2f}",
                "yes_price_dollars": f"{o['px'] if o['outcome'] == 'yes' else 1 - o['px']:.4f}"}


def make_cfg(**overrides) -> PilotConfig:
    cfg = load_config()
    cfg.loop.reconcile_every_s = 60
    cfg.selection.series_allowlist = []
    for k, v in overrides.items():
        sec, name = k.split("__")
        setattr(getattr(cfg, sec), name, v)
    return cfg.validate()


ARMED = LiveGate(cli_live=True, env_ok=True, config_live=True)
DISARMED = LiveGate(cli_live=False, env_ok=False, config_live=False)


def make_pilot(gate: LiveGate = ARMED, cfg: Optional[PilotConfig] = None, ex: Optional[FakeExchange] = None,
               signer: Any = "fake", account_reads: Optional[bool] = None):
    clock = ex.clock if ex else FakeClock()
    ex = ex or FakeExchange(clock)
    cfg = cfg or make_cfg()
    client = KalshiClient(cfg.api.base_url, gate, transport=ex,
                          signer=FakeSigner() if signer == "fake" else signer,
                          read_bucket=TokenBucket(1e9, 1e9), write_bucket=TokenBucket(1e9, 1e9),
                          sleep=lambda s: None, wall_clock=clock)
    journal = Journal(None, clock=clock)
    pilot = Pilot(cfg, gate, client, journal, run_id="test01", clock=clock, account_reads=account_reads)
    return pilot, ex, clock, journal
