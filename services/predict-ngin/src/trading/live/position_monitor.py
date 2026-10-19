"""
Position Monitoring Module

Real-time position tracking with P/L calculation, alerts, and risk limits.

Features:
- Real-time price updates via WebSocket
- P/L calculation (unrealized and realized)
- Alert system (Slack, Discord, email, console)
- Risk limit enforcement
- Daily/weekly/total drawdown tracking
- Position aging and expiry alerts

Usage:
    python -m src.trading.live.position_monitor --start
    python -m src.trading.live.position_monitor --status
"""

import json
import time
import asyncio
import threading
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Set
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import defaultdict
import queue

# WebSocket for real-time prices
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com"

MONITOR_STATE_PATH = "data/position_monitor_state.json"
ALERT_LOG_PATH = "data/alerts.jsonl"


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(Enum):
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    DRAWDOWN_WARNING = "drawdown_warning"
    DRAWDOWN_CRITICAL = "drawdown_critical"
    POSITION_AGING = "position_aging"
    RISK_LIMIT = "risk_limit"
    MARKET_RESOLVED = "market_resolved"
    CONNECTION_LOST = "connection_lost"


@dataclass
class Alert:
    """An alert notification."""
    timestamp: datetime
    level: AlertLevel
    alert_type: AlertType
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False


@dataclass
class PositionSnapshot:
    """Snapshot of a position for monitoring."""
    position_id: str
    market_id: str
    market_name: str
    outcome: str
    side: str
    entry_price: float
    current_price: float
    size_usd: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    entry_time: datetime
    holding_hours: float
    risk_score: float  # 0-1, higher = more risky


@dataclass
class PortfolioSnapshot:
    """Snapshot of entire portfolio."""
    timestamp: datetime
    total_equity: float
    cash: float
    unrealized_pnl: float
    realized_pnl: float
    daily_pnl: float
    weekly_pnl: float
    total_pnl: float
    daily_drawdown: float
    weekly_drawdown: float
    total_drawdown: float
    open_positions: int
    win_rate: float
    positions: List[PositionSnapshot] = field(default_factory=list)


class RiskLimits:
    """Risk limit configuration."""

    def __init__(
        self,
        max_position_size: float = 1000,
        max_total_exposure: float = 10000,
        max_positions: int = 20,
        max_daily_drawdown: float = 0.05,
        max_weekly_drawdown: float = 0.10,
        max_total_drawdown: float = 0.20,
        max_holding_hours: int = 48,
        stop_loss_pct: float = 0.20,
        take_profit_pct: float = 0.30,
    ):
        self.max_position_size = max_position_size
        self.max_total_exposure = max_total_exposure
        self.max_positions = max_positions
        self.max_daily_drawdown = max_daily_drawdown
        self.max_weekly_drawdown = max_weekly_drawdown
        self.max_total_drawdown = max_total_drawdown
        self.max_holding_hours = max_holding_hours
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct


class AlertDispatcher:
    """Dispatches alerts to various channels."""

    def __init__(self):
        self.handlers: List[Callable[[Alert], None]] = []
        self.alert_history: List[Alert] = []
        self.alert_log_path = Path(ALERT_LOG_PATH)

    def add_handler(self, handler: Callable[[Alert], None]):
        """Add an alert handler."""
        self.handlers.append(handler)

    def dispatch(self, alert: Alert):
        """Dispatch alert to all handlers."""
        self.alert_history.append(alert)
        self._log_alert(alert)

        for handler in self.handlers:
            try:
                handler(alert)
            except Exception as e:
                print(f"Alert handler error: {e}")

    def _log_alert(self, alert: Alert):
        """Log alert to file."""
        self.alert_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.alert_log_path, "a") as f:
            data = {
                "timestamp": alert.timestamp.isoformat(),
                "level": alert.level.value,
                "type": alert.alert_type.value,
                "message": alert.message,
                "data": alert.data,
            }
            f.write(json.dumps(data) + "\n")

    def get_recent_alerts(self, hours: int = 24) -> List[Alert]:
        """Get alerts from the last N hours."""
        cutoff = datetime.now() - timedelta(hours=hours)
        return [a for a in self.alert_history if a.timestamp > cutoff]


def console_alert_handler(alert: Alert):
    """Print alerts to console."""
    level_icons = {
        AlertLevel.INFO: "ℹ️",
        AlertLevel.WARNING: "⚠️",
        AlertLevel.CRITICAL: "🚨",
    }
    icon = level_icons.get(alert.level, "•")
    print(f"[{alert.timestamp.strftime('%H:%M:%S')}] {icon} {alert.message}")


def slack_alert_handler(webhook_url: str) -> Callable[[Alert], None]:
    """Create Slack alert handler."""
    def handler(alert: Alert):
        if alert.level == AlertLevel.INFO:
            return  # Only send warnings and critical

        color = "#ff0000" if alert.level == AlertLevel.CRITICAL else "#ffcc00"

        payload = {
            "attachments": [{
                "color": color,
                "title": f"{alert.alert_type.value.upper()}",
                "text": alert.message,
                "ts": int(alert.timestamp.timestamp()),
            }]
        }

        try:
            requests.post(webhook_url, json=payload, timeout=5)
        except:
            pass

    return handler


class PositionMonitor:
    """
    Real-time position monitoring system.

    Tracks positions, calculates P/L, enforces risk limits,
    and dispatches alerts.
    """

    def __init__(
        self,
        risk_limits: Optional[RiskLimits] = None,
        state_path: str = MONITOR_STATE_PATH,
    ):
        self.risk_limits = risk_limits or RiskLimits()
        self.state_path = Path(state_path)

        self.positions: Dict[str, Dict] = {}
        self.prices: Dict[str, float] = {}
        self.market_names: Dict[str, str] = {}

        self.daily_start_equity: float = 0
        self.weekly_start_equity: float = 0
        self.peak_equity: float = 0
        self.total_realized_pnl: float = 0
        self.trade_count: int = 0
        self.win_count: int = 0

        self.alert_dispatcher = AlertDispatcher()
        self.alert_dispatcher.add_handler(console_alert_handler)

        self._running = False
        self._price_lock = threading.Lock()
        self._last_check = datetime.now()

        # Load state
        self._load_state()

    def _load_state(self):
        """Load saved state."""
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    data = json.load(f)
                self.positions = data.get("positions", {})
                self.daily_start_equity = data.get("daily_start_equity", 0)
                self.weekly_start_equity = data.get("weekly_start_equity", 0)
                self.peak_equity = data.get("peak_equity", 0)
                self.total_realized_pnl = data.get("total_realized_pnl", 0)
            except Exception as e:
                print(f"Failed to load monitor state: {e}")

    def _save_state(self):
        """Save current state."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "positions": self.positions,
            "daily_start_equity": self.daily_start_equity,
            "weekly_start_equity": self.weekly_start_equity,
            "peak_equity": self.peak_equity,
            "total_realized_pnl": self.total_realized_pnl,
            "last_update": datetime.now().isoformat(),
        }
        with open(self.state_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def update_price(self, market_id: str, price: float):
        """Update price for a market."""
        with self._price_lock:
            self.prices[market_id] = price

    def get_price(self, market_id: str) -> Optional[float]:
        """Get current price for a market."""
        with self._price_lock:
            return self.prices.get(market_id)

    def add_position(
        self,
        position_id: str,
        market_id: str,
        side: str,
        entry_price: float,
        size_usd: float,
        market_name: str = "",
    ):
        """Add a position to monitor."""
        self.positions[position_id] = {
            "market_id": market_id,
            "side": side,
            "entry_price": entry_price,
            "size_usd": size_usd,
            "entry_time": datetime.now().isoformat(),
            "current_price": entry_price,
            "unrealized_pnl": 0,
        }

        if market_name:
            self.market_names[market_id] = market_name

        self.alert_dispatcher.dispatch(Alert(
            timestamp=datetime.now(),
            level=AlertLevel.INFO,
            alert_type=AlertType.POSITION_OPENED,
            message=f"Opened {side} ${size_usd:.0f} @ {entry_price:.3f} on {market_name[:30] if market_name else market_id[:20]}",
            data={"position_id": position_id, "market_id": market_id},
        ))

        self._save_state()

    def close_position(self, position_id: str, exit_price: float) -> Optional[float]:
        """Close a position and return realized P/L."""
        if position_id not in self.positions:
            return None

        pos = self.positions[position_id]

        if pos["side"].lower() == "buy":
            pnl = (exit_price - pos["entry_price"]) * pos["size_usd"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["size_usd"]

        self.total_realized_pnl += pnl
        self.trade_count += 1
        if pnl > 0:
            self.win_count += 1

        market_name = self.market_names.get(pos["market_id"], pos["market_id"][:20])

        self.alert_dispatcher.dispatch(Alert(
            timestamp=datetime.now(),
            level=AlertLevel.INFO,
            alert_type=AlertType.POSITION_CLOSED,
            message=f"Closed position on {market_name}: P/L ${pnl:+.2f}",
            data={"position_id": position_id, "pnl": pnl},
        ))

        del self.positions[position_id]
        self._save_state()

        return pnl

    def update_positions(self):
        """Update all positions with current prices."""
        for pos_id, pos in self.positions.items():
            market_id = pos["market_id"]
            current_price = self.get_price(market_id)

            if current_price is None:
                # Try to fetch
                current_price = self._fetch_price(market_id)
                if current_price:
                    self.update_price(market_id, current_price)

            if current_price:
                pos["current_price"] = current_price

                if pos["side"].lower() == "buy":
                    pos["unrealized_pnl"] = (current_price - pos["entry_price"]) * pos["size_usd"]
                else:
                    pos["unrealized_pnl"] = (pos["entry_price"] - current_price) * pos["size_usd"]

    def _fetch_price(self, market_id: str) -> Optional[float]:
        """Fetch price from API."""
        try:
            response = requests.get(
                f"{GAMMA_API}/markets/{market_id}",
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json()
                prices = data.get("outcomePrices", [])
                if prices:
                    return float(prices[0])
        except:
            pass
        return None

    def get_portfolio_snapshot(self, total_equity: float) -> PortfolioSnapshot:
        """Get current portfolio snapshot."""
        self.update_positions()

        # Calculate P/L
        unrealized = sum(p["unrealized_pnl"] for p in self.positions.values())
        cash = total_equity - sum(p["size_usd"] for p in self.positions.values())

        # Drawdowns
        if total_equity > self.peak_equity:
            self.peak_equity = total_equity

        total_dd = (self.peak_equity - total_equity) / self.peak_equity if self.peak_equity > 0 else 0

        daily_pnl = total_equity - self.daily_start_equity if self.daily_start_equity > 0 else 0
        daily_dd = -daily_pnl / self.daily_start_equity if self.daily_start_equity > 0 and daily_pnl < 0 else 0

        weekly_pnl = total_equity - self.weekly_start_equity if self.weekly_start_equity > 0 else 0
        weekly_dd = -weekly_pnl / self.weekly_start_equity if self.weekly_start_equity > 0 and weekly_pnl < 0 else 0

        # Position snapshots
        position_snapshots = []
        for pos_id, pos in self.positions.items():
            entry_time = datetime.fromisoformat(pos["entry_time"])
            holding_hours = (datetime.now() - entry_time).total_seconds() / 3600

            pnl_pct = pos["unrealized_pnl"] / pos["size_usd"] if pos["size_usd"] > 0 else 0

            # Risk score: higher for older positions, larger losses
            risk_score = min(1.0, (
                0.3 * min(holding_hours / self.risk_limits.max_holding_hours, 1) +
                0.4 * max(0, -pnl_pct / self.risk_limits.stop_loss_pct) +
                0.3 * (pos["size_usd"] / self.risk_limits.max_position_size)
            ))

            position_snapshots.append(PositionSnapshot(
                position_id=pos_id,
                market_id=pos["market_id"],
                market_name=self.market_names.get(pos["market_id"], ""),
                outcome="YES",
                side=pos["side"],
                entry_price=pos["entry_price"],
                current_price=pos["current_price"],
                size_usd=pos["size_usd"],
                unrealized_pnl=pos["unrealized_pnl"],
                unrealized_pnl_pct=pnl_pct,
                entry_time=entry_time,
                holding_hours=holding_hours,
                risk_score=risk_score,
            ))

        return PortfolioSnapshot(
            timestamp=datetime.now(),
            total_equity=total_equity,
            cash=cash,
            unrealized_pnl=unrealized,
            realized_pnl=self.total_realized_pnl,
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            total_pnl=self.total_realized_pnl + unrealized,
            daily_drawdown=daily_dd,
            weekly_drawdown=weekly_dd,
            total_drawdown=total_dd,
            open_positions=len(self.positions),
            win_rate=self.win_count / self.trade_count if self.trade_count > 0 else 0,
            positions=position_snapshots,
        )

    def check_risk_limits(self, snapshot: PortfolioSnapshot) -> List[Alert]:
        """Check all risk limits and return any violations."""
        alerts = []

        # Drawdown checks
        if snapshot.daily_drawdown >= self.risk_limits.max_daily_drawdown:
            alerts.append(Alert(
                timestamp=datetime.now(),
                level=AlertLevel.CRITICAL,
                alert_type=AlertType.DRAWDOWN_CRITICAL,
                message=f"DAILY DRAWDOWN LIMIT: {snapshot.daily_drawdown:.1%} >= {self.risk_limits.max_daily_drawdown:.1%}",
                data={"drawdown": snapshot.daily_drawdown},
            ))
        elif snapshot.daily_drawdown >= self.risk_limits.max_daily_drawdown * 0.8:
            alerts.append(Alert(
                timestamp=datetime.now(),
                level=AlertLevel.WARNING,
                alert_type=AlertType.DRAWDOWN_WARNING,
                message=f"Daily drawdown warning: {snapshot.daily_drawdown:.1%}",
                data={"drawdown": snapshot.daily_drawdown},
            ))

        if snapshot.total_drawdown >= self.risk_limits.max_total_drawdown:
            alerts.append(Alert(
                timestamp=datetime.now(),
                level=AlertLevel.CRITICAL,
                alert_type=AlertType.DRAWDOWN_CRITICAL,
                message=f"TOTAL DRAWDOWN LIMIT: {snapshot.total_drawdown:.1%} >= {self.risk_limits.max_total_drawdown:.1%}",
                data={"drawdown": snapshot.total_drawdown},
            ))

        # Position checks
        for pos in snapshot.positions:
            # Stop loss
            if pos.unrealized_pnl_pct <= -self.risk_limits.stop_loss_pct:
                alerts.append(Alert(
                    timestamp=datetime.now(),
                    level=AlertLevel.CRITICAL,
                    alert_type=AlertType.STOP_LOSS,
                    message=f"STOP LOSS: {pos.market_name[:20]} at {pos.unrealized_pnl_pct:.1%}",
                    data={"position_id": pos.position_id, "pnl_pct": pos.unrealized_pnl_pct},
                ))

            # Take profit
            if pos.unrealized_pnl_pct >= self.risk_limits.take_profit_pct:
                alerts.append(Alert(
                    timestamp=datetime.now(),
                    level=AlertLevel.INFO,
                    alert_type=AlertType.PROFIT_TARGET,
                    message=f"Take profit target: {pos.market_name[:20]} at {pos.unrealized_pnl_pct:.1%}",
                    data={"position_id": pos.position_id, "pnl_pct": pos.unrealized_pnl_pct},
                ))

            # Position aging
            if pos.holding_hours >= self.risk_limits.max_holding_hours:
                alerts.append(Alert(
                    timestamp=datetime.now(),
                    level=AlertLevel.WARNING,
                    alert_type=AlertType.POSITION_AGING,
                    message=f"Position aging: {pos.market_name[:20]} held for {pos.holding_hours:.0f}h",
                    data={"position_id": pos.position_id, "hours": pos.holding_hours},
                ))

        # Dispatch alerts
        for alert in alerts:
            self.alert_dispatcher.dispatch(alert)

        return alerts

    def print_status(self, snapshot: PortfolioSnapshot):
        """Print formatted status."""
        print("\n" + "=" * 60)
        print("PORTFOLIO MONITOR")
        print("=" * 60)
        print(f"Timestamp: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Total Equity: ${snapshot.total_equity:,.2f}")
        print(f"Cash: ${snapshot.cash:,.2f}")
        print(f"Unrealized P/L: ${snapshot.unrealized_pnl:+,.2f}")
        print(f"Realized P/L: ${snapshot.realized_pnl:+,.2f}")
        print(f"Total P/L: ${snapshot.total_pnl:+,.2f}")
        print("-" * 60)
        print(f"Daily P/L: ${snapshot.daily_pnl:+,.2f} | DD: {snapshot.daily_drawdown:.1%}")
        print(f"Weekly P/L: ${snapshot.weekly_pnl:+,.2f} | DD: {snapshot.weekly_drawdown:.1%}")
        print(f"Total DD: {snapshot.total_drawdown:.1%}")
        print("-" * 60)
        print(f"Open Positions: {snapshot.open_positions}")
        print(f"Win Rate: {snapshot.win_rate:.1%}")
        print("=" * 60)

        if snapshot.positions:
            print("\nOPEN POSITIONS:")
            print("-" * 60)
            for pos in sorted(snapshot.positions, key=lambda p: p.risk_score, reverse=True):
                risk_indicator = "🔴" if pos.risk_score > 0.7 else "🟡" if pos.risk_score > 0.4 else "🟢"
                print(f"{risk_indicator} {pos.market_name[:35] or pos.market_id[:35]}...")
                print(f"   {pos.side} ${pos.size_usd:.0f} @ {pos.entry_price:.3f} → {pos.current_price:.3f}")
                print(f"   P/L: ${pos.unrealized_pnl:+.2f} ({pos.unrealized_pnl_pct:+.1%}) | {pos.holding_hours:.0f}h")

    def run_monitor_loop(self, get_equity_func: Callable[[], float], interval: float = 10):
        """
        Main monitoring loop.

        Args:
            get_equity_func: Function that returns current total equity
            interval: Seconds between checks
        """
        self._running = True
        print("Position monitor started. Press Ctrl+C to stop.")

        # Initialize daily/weekly equity
        equity = get_equity_func()
        if self.daily_start_equity == 0:
            self.daily_start_equity = equity
        if self.weekly_start_equity == 0:
            self.weekly_start_equity = equity
        if self.peak_equity == 0:
            self.peak_equity = equity

        last_day = datetime.now().date()
        last_week = datetime.now().isocalendar()[1]

        try:
            while self._running:
                # Check for day/week rollover
                now = datetime.now()
                if now.date() != last_day:
                    self.daily_start_equity = get_equity_func()
                    last_day = now.date()
                    print(f"New day - reset daily equity to ${self.daily_start_equity:,.2f}")

                if now.isocalendar()[1] != last_week:
                    self.weekly_start_equity = get_equity_func()
                    last_week = now.isocalendar()[1]
                    print(f"New week - reset weekly equity to ${self.weekly_start_equity:,.2f}")

                # Get snapshot and check limits
                equity = get_equity_func()
                snapshot = self.get_portfolio_snapshot(equity)
                self.check_risk_limits(snapshot)

                # Print status periodically
                if (now - self._last_check).total_seconds() >= 60:
                    self.print_status(snapshot)
                    self._last_check = now

                self._save_state()
                time.sleep(interval)

        except KeyboardInterrupt:
            print("\nStopping monitor...")
        finally:
            self._running = False
            self._save_state()

    def stop(self):
        """Stop the monitoring loop."""
        self._running = False


def main():
    """Test position monitor."""
    import sys

    monitor = PositionMonitor()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m src.trading.live.position_monitor --status")
        print("  python -m src.trading.live.position_monitor --start")
        print("  python -m src.trading.live.position_monitor --test")
        return

    command = sys.argv[1]

    if command == "--status":
        # Get equity from paper trading state if available
        paper_state_path = Path("data/paper_trading_state.json")
        equity = 10000
        if paper_state_path.exists():
            with open(paper_state_path) as f:
                data = json.load(f)
                equity = data.get("cash", 10000) + sum(
                    p.get("size_usd", 0) + p.get("unrealized_pnl", 0)
                    for p in data.get("positions", {}).values()
                )

        snapshot = monitor.get_portfolio_snapshot(equity)
        monitor.print_status(snapshot)

    elif command == "--start":
        def get_equity():
            paper_state_path = Path("data/paper_trading_state.json")
            if paper_state_path.exists():
                with open(paper_state_path) as f:
                    data = json.load(f)
                    return data.get("cash", 10000) + sum(
                        p.get("size_usd", 0) + p.get("unrealized_pnl", 0)
                        for p in data.get("positions", {}).values()
                    )
            return 10000

        monitor.run_monitor_loop(get_equity)

    elif command == "--test":
        print("Testing position monitor...")

        # Add test position
        monitor.add_position(
            position_id="TEST-001",
            market_id="test-market",
            side="buy",
            entry_price=0.5,
            size_usd=100,
            market_name="Test Market Question",
        )

        # Simulate price update
        monitor.update_price("test-market", 0.55)
        monitor.update_positions()

        # Get snapshot
        snapshot = monitor.get_portfolio_snapshot(10000)
        monitor.print_status(snapshot)

        # Check alerts
        alerts = monitor.check_risk_limits(snapshot)
        print(f"\nAlerts generated: {len(alerts)}")


if __name__ == "__main__":
    main()
