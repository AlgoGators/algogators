"""Broker: the one object strategies talk to. Modes:

* ``live``    - real orders. Requires ``live=True`` AND every gate armed; otherwise
  ``MutationBlocked`` is raised (never a silent fall-back to dry run).
* ``paper``   - orders rest in memory and fill against the venue's public data
  (:mod:`.paper`); no keys needed.
* ``dry_run`` - nothing is sent and nothing fills; the journal records what would have
  happened (the default).

Every order passes :func:`.limits.check` first, in every mode, so a strategy behaves the same in
paper and live.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional

from .gate import LiveGate, MutationBlocked, load_config
from .journal import Journal
from .limits import LimitState, RiskLimits, check, record_closed, record_placed
from .orders import OrderAck, OrderIntent, new_client_order_id


class Broker:
    def __init__(
        self,
        venue: str,
        mode: str,
        gate: LiveGate,
        limits: RiskLimits,
        trader=None,
        paper=None,
        journal_dir: Optional[Path] = None,
    ):
        self.venue, self.mode, self.gate, self.limits = venue, mode, gate, limits
        self.trader, self.paper = trader, paper
        self.journal = Journal(venue, mode, journal_dir)
        self.state = LimitState()
        self.orders: Dict[str, OrderIntent] = {}  # order_id -> intent (resting, as far as we know)
        self.journal.write("broker_open", limits=vars(limits))

    def place(self, intent: OrderIntent) -> OrderAck:
        if not intent.client_order_id:
            intent = OrderIntent(**{**vars(intent), "client_order_id": new_client_order_id()})
        if intent.venue != self.venue:
            raise ValueError(f"intent for {intent.venue} sent to the {self.venue} broker")
        self.journal.write("intent", **vars(intent))
        try:
            check(intent, self.limits, self.state)
        except Exception as e:
            self.journal.write(
                "rejected_by_limits", client_order_id=intent.client_order_id, reason=str(e)
            )
            raise
        if self.mode == "dry_run":
            self.journal.write("would_place", client_order_id=intent.client_order_id)
            return OrderAck(
                self.venue,
                intent.market,
                intent.client_order_id,
                None,
                "dry_run",
                remaining=intent.quantity,
            )
        if self.mode == "paper":
            ack = self.paper.place(intent)
            self.journal.write(
                "placed" if ack.order_id else "rejected",
                client_order_id=intent.client_order_id,
                order_id=ack.order_id,
                status=ack.status,
                **(ack.raw or {}),
            )
            if not ack.order_id:
                return ack
        else:
            reply, body = self.trader.place(intent, live=True)
            raw = reply.body if isinstance(reply.body, dict) else {"body": reply.body}
            order = raw.get("order", raw)
            oid = order.get("order_id") or order.get("id") or raw.get("id")
            filled = float(order.get("fill_count") or order.get("fill_count_fp") or 0)
            remaining = float(
                order.get("remaining_count")
                or order.get("remaining_count_fp")
                or (intent.quantity - filled)
            )
            ack = OrderAck(
                self.venue,
                intent.market,
                intent.client_order_id,
                oid,
                "resting" if remaining > 0 else "filled",
                filled,
                remaining,
                reply.ms,
                raw,
            )
            self.journal.write(
                "placed",
                client_order_id=intent.client_order_id,
                order_id=oid,
                request=body,
                response=raw,
                ms=round(reply.ms, 1),
            )
        record_placed(intent, self.state)
        if ack.order_id:
            self.orders[ack.order_id] = intent
        return ack

    def cancel(self, order_id: str) -> Optional[float]:
        intent = self.orders.get(order_id)
        market = intent.market if intent else None
        if self.mode == "dry_run":
            self.journal.write("would_cancel", order_id=order_id)
            return None
        if self.mode == "paper":
            left = self.paper.cancel(order_id)
            ms = 0.0
        else:
            reply = self.trader.cancel(order_id, market, live=True)
            raw = reply.body if isinstance(reply.body, dict) else {}
            left = float(raw.get("reduced_by") or raw.get("reduced_by_fp") or 0) or None
            ms = reply.ms
        self.journal.write("cancelled", order_id=order_id, reduced_by=left, ms=round(ms, 1))
        if intent is not None:
            record_closed(intent, self.state, left or 0.0)
            self.orders.pop(order_id, None)
        return left

    def poll(self) -> List[dict]:
        """Paper mode: advance resting paper orders against public data; journal and return fills.

        Filled-out orders release their limit slots. Other modes return [] (live fills come from
        the venue's own fill history).
        """
        if self.mode != "paper":
            return []
        fills = self.paper.poll()
        for f in fills:
            # the journal sets its own "ts"; keep the fill time as fill_ts
            self.journal.write("fill", **{("fill_ts" if k == "ts" else k): v for k, v in f.items()})
            if f["order_id"] not in self.paper.orders:
                intent = self.orders.pop(f["order_id"], None)
                if intent is not None:
                    record_closed(intent, self.state, 0.0)
        return fills

    def cancel_all(self) -> int:
        """Cancel every order this process placed (live: every resting order on the account)."""
        ids: List[str] = list(self.orders)
        if self.mode == "live":
            body = self.trader.open_orders().body
            for o in (body.get("orders") or []) if isinstance(body, dict) else []:
                oid = o.get("order_id") or o.get("id")
                if oid and oid not in ids:
                    ids.append(oid)
        for oid in ids:
            try:
                self.cancel(oid)
            except Exception as e:  # keep cancelling the rest
                self.journal.write("cancel_error", order_id=oid, error=str(e)[:300])
        self.journal.write("cancel_all", n=len(ids))
        return len(ids)


def make_broker(
    venue: str,
    *,
    live: bool = False,
    paper: bool = False,
    gate: Optional[LiveGate] = None,
    limits: Optional[RiskLimits] = None,
    journal_dir: Optional[Path] = None,
    paper_opts: Optional[dict] = None,
) -> Broker:
    """``limits`` and ``journal_dir`` apply to paper and dry-run brokers only: a live broker always
    uses the limits of ``config/live.local.yaml`` and the live journal."""
    gate = gate or LiveGate(venue)
    cfg_limits = RiskLimits.from_config(load_config().get("limits"))
    if live:
        if limits is not None or journal_dir is not None:
            raise ValueError("a live broker takes its limits and journal from the live config")
        why = gate.reasons(True)
        if why:
            raise MutationBlocked("live broker refused: " + "; ".join(why))
        from .venues import make_trader

        return Broker(venue, "live", gate, cfg_limits, trader=make_trader(venue, gate))
    limits = limits or cfg_limits
    if paper:
        from .paper import PaperBook

        book = PaperBook(venue, **(paper_opts or {}))
        return Broker(venue, "paper", gate, limits, paper=book, journal_dir=journal_dir)
    return Broker(venue, "dry_run", gate, limits, journal_dir=journal_dir)


def now_ms() -> float:
    return time.time() * 1000
