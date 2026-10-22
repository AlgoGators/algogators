"""``predict-ngin trade ...``: gates, latency test, cancel-all and the kill switch.

    uv run predict-ngin trade status [--venue kalshi]   # gates, kill switch, limits, balance
    uv run predict-ngin trade latency-test --venue kalshi [--market T] [--trials 3] --live
    uv run predict-ngin trade cancel-all --venue kalshi --live
    uv run predict-ngin trade kill on|off               # on: no new orders; cancels allowed

``status`` is read-only.
"""

from __future__ import annotations

import argparse
import json
import sys

from .gate import CONFIG, ENV_VAR, LiveGate, MutationBlocked, config_path, load_config, stop_path
from .limits import RiskLimits


def status(venue: str) -> int:
    g = LiveGate(venue)
    why = g.reasons(True)
    print(f"venue {venue}: live {'ARMED (with --live)' if not why else 'blocked'}")
    for r in why:
        print(f"  - {r}")
    print(f"kill switch: {g.kill_reason() or 'off'}")
    print(f"limits: {vars(RiskLimits.from_config(load_config().get('limits')))}")
    try:
        from .venues import make_trader

        t = make_trader(venue, g)
        b = t.balance()
        print(f"balance ({b.ms:.0f} ms): {json.dumps(b.body)[:300]}")
    except Exception as e:  # no keys yet, or network
        print(f"balance: unavailable ({type(e).__name__}: {str(e)[:160]})")
    print(
        f"to arm live trading: set {ENV_VAR}=1, create {CONFIG.as_posix()} "
        "(see docs/EXECUTION.md), pass --live"
    )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="predict-ngin trade", description="order execution (live is off by default)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--venue", default="kalshi", choices=["kalshi", "polymarket_us"])
    lt = sub.add_parser("latency-test")
    lt.add_argument("--venue", default="kalshi", choices=["kalshi", "polymarket_us"])
    lt.add_argument("--market")
    lt.add_argument("--trials", type=int, default=3)
    lt.add_argument("--price", type=float, default=0.01)
    lt.add_argument("--live", action="store_true")
    ca = sub.add_parser("cancel-all")
    ca.add_argument("--venue", default="kalshi", choices=["kalshi", "polymarket_us"])
    ca.add_argument("--live", action="store_true")
    k = sub.add_parser("kill")
    k.add_argument("state", choices=["on", "off"])
    a = ap.parse_args(argv)
    try:
        if a.cmd == "status":
            return status(a.venue)
        if a.cmd == "kill":
            p = stop_path()
            if a.state == "on":
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("kill switch on\n")
            elif p.exists():
                p.unlink()
            print(f"kill switch {a.state} ({p})")
            return 0
        if a.cmd == "latency-test":
            from .latency import run

            print(json.dumps(run(a.venue, a.market, a.trials, a.price, live=a.live), indent=1))
            return 0
        if a.cmd == "cancel-all":
            from .brokers import make_broker

            print(f"cancelled {make_broker(a.venue, live=a.live).cancel_all()} order(s)")
            return 0
    except MutationBlocked as e:
        print(f"refused: {e}", file=sys.stderr)
        print(f"(config file: {config_path()})", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
