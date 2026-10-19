"""Order execution for Kalshi and Polymarket US: gated live clients, risk limits, an audit journal
and paper mode.

Live trading is OFF by default. Every state-changing call needs all three gates at that moment
(:mod:`predict_ngin.execution.gate`):

1. the environment variable ``PREDICT_NGIN_LIVE=1``;
2. ``config/live.local.yaml`` (gitignored, machine-local) with ``live.enabled: true`` and the
   venue listed;
3. an explicit ``live=True`` from the caller (the ``--live`` flag on the command line).

A kill switch (``<data>/live/STOP`` or ``kill: true`` in the config) blocks new orders but still
allows cancels. Every intent, request and response is appended to
``<data>/live/journal/YYYY-MM-DD.jsonl``. Every order passes
:class:`~predict_ngin.execution.limits.RiskLimits` first (per-order, per-day, per-market and
open-order caps; post-only by default). Without the gates, brokers run in dry-run mode and only
journal what they would do.

    from predict_ngin.execution import OrderIntent, open_broker
    broker = open_broker("kalshi")  # dry run; paper=True or live=True (all gates) otherwise
    ack = broker.place(OrderIntent(venue="kalshi", market="KXA-1", outcome="yes",
                                   price=0.42, quantity=1))

Command line: ``uv run predict-ngin trade status | latency-test | cancel-all | kill``. See
docs/EXECUTION.md.
"""

from .gate import KillSwitchOn, LiveGate, MutationBlocked
from .journal import Journal
from .limits import LimitBreached, RiskLimits
from .orders import OrderAck, OrderIntent

__all__ = [
    "Journal",
    "KillSwitchOn",
    "LimitBreached",
    "LiveGate",
    "MutationBlocked",
    "OrderAck",
    "OrderIntent",
    "RiskLimits",
    "open_broker",
]


def open_broker(
    venue: str, *, live: bool = False, paper: bool = False, gate: "LiveGate | None" = None, **kw
):
    """Broker for ``venue`` (kalshi or polymarket_us): paper, live (all gates armed) or dry run.

    Paper and dry-run brokers also take ``limits`` (a :class:`RiskLimits`), ``journal_dir`` and
    ``paper_opts`` (e.g. ``{"max_rps": 8}``); see :func:`.brokers.make_broker`."""
    from .brokers import make_broker

    return make_broker(venue, live=live, paper=paper, gate=gate, **kw)
