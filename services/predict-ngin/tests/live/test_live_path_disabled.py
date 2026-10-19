"""The broken Polymarket live order path is hard-disabled (plan step 7, 2026-10-15)."""
import subprocess
import sys
from pathlib import Path

import pytest

from trading.live.order_router import LiveTradingDisabledError, OrderRouter

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "live" / "run_live_strategy.py"


def test_order_router_live_raises():
    with pytest.raises(LiveTradingDisabledError, match="Live Polymarket trading is disabled"):
        OrderRouter(dry_run=False)


def test_order_router_dry_run_still_works():
    assert OrderRouter(dry_run=True).dry_run is True


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)


def test_run_live_strategy_rejects_live_flag():
    r = _run("--live")
    assert r.returncode == 2
    assert "--live is removed" in r.stderr


def test_run_live_strategy_help_hides_live_flag():
    r = _run("--help")
    assert r.returncode == 0
    assert "--live" not in r.stdout
