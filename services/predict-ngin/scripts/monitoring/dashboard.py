#!/usr/bin/env python3
"""
Terminal Dashboard for Monitoring Positions, PnL, and Risk

Provides a real-time or snapshot view of trading performance.
"""

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import sys
import time

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Warning: rich library not available. Install with: pip install rich")

from src.config import get_config
from src.trading.reporting import build_run_summary_from_trades, RunSummary


def load_paper_trading_log(log_path: str) -> List[Dict]:
    """Load paper trading log entries."""
    if not Path(log_path).exists():
        return []
    
    entries = []
    with open(log_path, "r") as f:
        for line in f:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    
    return entries


def load_execution_log(log_path: str) -> List[Dict]:
    """Load execution log entries."""
    if not Path(log_path).exists():
        return []
    
    entries = []
    with open(log_path, "r") as f:
        for line in f:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    
    return entries


def calculate_portfolio_summary(entries: List[Dict]) -> Dict:
    """Calculate portfolio summary from log entries."""
    if not entries:
        return {
            "total_trades": 0,
            "open_positions": 0,
            "total_pnl": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_capital": 0.0,
            "deployed_capital": 0.0,
            "available_capital": 0.0,
        }
    
    # Filter for recent entries (last 24 hours)
    cutoff = datetime.utcnow() - timedelta(hours=24)
    
    recent_entries = [
        e for e in entries
        if "timestamp" in e and datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")) > cutoff
    ]
    
    total_trades = len([e for e in entries if e.get("type") == "trade"])
    open_positions = len([e for e in entries if e.get("status") == "open"])
    
    total_pnl = sum(e.get("pnl", 0) for e in entries if "pnl" in e)
    realized_pnl = sum(
        e.get("pnl", 0) for e in entries
        if e.get("status") == "closed" and "pnl" in e
    )
    unrealized_pnl = sum(
        e.get("unrealized_pnl", 0) for e in entries
        if e.get("status") == "open" and "unrealized_pnl" in e
    )
    
    # Get capital info from latest entry
    latest_entry = entries[-1] if entries else {}
    total_capital = latest_entry.get("total_capital", 0.0)
    deployed_capital = latest_entry.get("deployed_capital", 0.0)
    available_capital = latest_entry.get("available_capital", 0.0)
    
    return {
        "total_trades": total_trades,
        "open_positions": open_positions,
        "total_pnl": total_pnl,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_capital": total_capital,
        "deployed_capital": deployed_capital,
        "available_capital": available_capital,
        "recent_trades": len(recent_entries) if recent_entries else 0,
    }


def create_dashboard_table(summary: Dict, use_rich: bool = True) -> str:
    """Create dashboard table."""
    if use_rich and RICH_AVAILABLE:
        console = Console()
        
        # Portfolio Summary Table
        portfolio_table = Table(title="Portfolio Summary", box=box.ROUNDED)
        portfolio_table.add_column("Metric", style="cyan")
        portfolio_table.add_column("Value", style="green")
        
        portfolio_table.add_row("Total Capital", f"${summary['total_capital']:,.2f}")
        portfolio_table.add_row("Deployed Capital", f"${summary['deployed_capital']:,.2f}")
        portfolio_table.add_row("Available Capital", f"${summary['available_capital']:,.2f}")
        portfolio_table.add_row("", "")
        portfolio_table.add_row("Total Trades", str(summary['total_trades']))
        portfolio_table.add_row("Open Positions", str(summary['open_positions']))
        portfolio_table.add_row("Recent Trades (24h)", str(summary['recent_trades']))
        
        # PnL Table
        pnl_table = Table(title="Profit & Loss", box=box.ROUNDED)
        pnl_table.add_column("Type", style="cyan")
        pnl_table.add_column("Amount", style="green")
        
        pnl_color = "green" if summary['total_pnl'] >= 0 else "red"
        pnl_table.add_row("Total PnL", f"[{pnl_color}]${summary['total_pnl']:,.2f}[/{pnl_color}]")
        pnl_table.add_row("Realized PnL", f"${summary['realized_pnl']:,.2f}")
        pnl_table.add_row("Unrealized PnL", f"${summary['unrealized_pnl']:,.2f}")
        
        # Create layout
        layout = Layout()
        layout.split_column(
            Layout(Panel(portfolio_table, title="Portfolio")),
            Layout(Panel(pnl_table, title="Performance")),
        )
        
        return layout
    else:
        # Plain text version
        lines = []
        lines.append("=" * 50)
        lines.append("PORTFOLIO SUMMARY")
        lines.append("=" * 50)
        lines.append(f"Total Capital:        ${summary['total_capital']:,.2f}")
        lines.append(f"Deployed Capital:     ${summary['deployed_capital']:,.2f}")
        lines.append(f"Available Capital:    ${summary['available_capital']:,.2f}")
        lines.append("")
        lines.append(f"Total Trades:          {summary.get('total_trades', 0)}")
        lines.append(f"Open Positions:       {summary.get('open_positions', 0)}")
        lines.append(f"Recent Trades (24h):  {summary.get('recent_trades', 0)}")
        lines.append("")
        lines.append("=" * 50)
        lines.append("PROFIT & LOSS")
        lines.append("=" * 50)
        lines.append(f"Total PnL:            ${summary['total_pnl']:,.2f}")
        lines.append(f"Realized PnL:        ${summary['realized_pnl']:,.2f}")
        lines.append(f"Unrealized PnL:      ${summary['unrealized_pnl']:,.2f}")
        lines.append("=" * 50)
        
        return "\n".join(lines)


def display_dashboard(
    paper_log_path: Optional[str] = None,
    execution_log_path: Optional[str] = None,
    live: bool = False,
    refresh_interval: float = 5.0,
) -> None:
    """Display dashboard."""
    config = get_config()
    
    if paper_log_path is None:
        paper_log_path = config.logging.execution.path.replace("execution_log.jsonl", "paper_trading_log.jsonl")
    
    if execution_log_path is None:
        execution_log_path = config.logging.execution.path
    
    use_rich = RICH_AVAILABLE and not live  # Use rich for static, plain for live updates
    
    if live and RICH_AVAILABLE:
        console = Console()
        
        def generate_dashboard():
            paper_entries = load_paper_trading_log(paper_log_path)
            exec_entries = load_execution_log(execution_log_path)
            
            # Combine entries
            all_entries = paper_entries + exec_entries
            
            summary = calculate_portfolio_summary(all_entries)
            return create_dashboard_table(summary, use_rich=True)
        
        with Live(generate_dashboard(), refresh_per_second=1/refresh_interval, screen=True) as live_display:
            try:
                while True:
                    time.sleep(refresh_interval)
                    live_display.update(generate_dashboard())
            except KeyboardInterrupt:
                pass
    else:
        # Static display
        paper_entries = load_paper_trading_log(paper_log_path)
        exec_entries = load_execution_log(execution_log_path)
        
        all_entries = paper_entries + exec_entries
        summary = calculate_portfolio_summary(all_entries)
        
        dashboard = create_dashboard_table(summary, use_rich=RICH_AVAILABLE)
        
        if RICH_AVAILABLE:
            console = Console()
            console.print(dashboard)
        else:
            print(dashboard)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Display trading dashboard"
    )
    parser.add_argument(
        "--paper-log",
        type=str,
        default=None,
        help="Path to paper trading log (default: from config)"
    )
    parser.add_argument(
        "--execution-log",
        type=str,
        default=None,
        help="Path to execution log (default: from config)"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Live updating dashboard"
    )
    parser.add_argument(
        "--refresh-interval",
        type=float,
        default=5.0,
        help="Refresh interval in seconds for live mode (default: 5.0)"
    )
    
    args = parser.parse_args()
    
    display_dashboard(
        paper_log_path=args.paper_log,
        execution_log_path=args.execution_log,
        live=args.live,
        refresh_interval=args.refresh_interval,
    )


if __name__ == "__main__":
    main()
