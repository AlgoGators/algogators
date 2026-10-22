"""
Execution Logger

Tracks execution quality and compares against backtest assumptions.
Measures slippage, fill rates, latency, and other execution metrics.

Features:
- Trade-by-trade execution logging
- Slippage analysis (expected vs actual)
- Latency tracking (signal to fill)
- Cost analysis (fees, spread, impact)
- Comparison with backtest assumptions
- Daily/weekly execution reports

Usage:
    from src.trading.live.execution_logger import ExecutionLogger

    logger = ExecutionLogger()
    logger.log_execution(...)
    logger.generate_report()
"""

import json
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from collections import defaultdict

EXECUTION_LOG_PATH = "data/execution_log.jsonl"
EXECUTION_DB_PATH = "data/execution_metrics.db"
REPORT_OUTPUT_DIR = Path("data/research_reports")


@dataclass
class ExecutionRecord:
    """Record of a single execution."""
    execution_id: str
    timestamp: datetime
    market_id: str
    token_id: str
    side: str  # buy or sell
    order_type: str  # market, limit

    # Size
    requested_size_usd: float
    filled_size_usd: float
    fill_rate: float

    # Pricing
    signal_price: float  # Price when signal generated
    expected_price: float  # Backtest model price
    actual_price: float  # Actual execution price

    # Slippage
    signal_slippage: float  # actual vs signal
    expected_slippage: float  # actual vs expected
    slippage_bps: float  # In basis points

    # Costs
    spread_cost: float
    impact_cost: float
    fees: float
    total_cost: float
    total_cost_bps: float

    # Timing
    signal_time: datetime
    order_time: datetime
    fill_time: datetime
    signal_to_order_ms: float
    order_to_fill_ms: float
    total_latency_ms: float

    # Market conditions
    bid_price: float
    ask_price: float
    spread: float
    liquidity_estimate: float

    # Metadata
    signal_source: str
    strategy: str
    notes: str = ""


@dataclass
class ExecutionSummary:
    """Summary statistics for executions."""
    period_start: datetime
    period_end: datetime
    total_executions: int
    total_volume_usd: float

    # Fill rates
    avg_fill_rate: float
    full_fill_pct: float  # % of orders fully filled

    # Slippage
    avg_slippage_bps: float
    median_slippage_bps: float
    max_slippage_bps: float
    slippage_std_bps: float

    # Costs
    avg_cost_bps: float
    total_fees_usd: float
    total_spread_cost_usd: float
    total_impact_cost_usd: float

    # Latency
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float

    # vs Backtest
    actual_vs_expected_slippage: float  # ratio
    cost_model_accuracy: float  # correlation


class ExecutionLogger:
    """
    Logs and analyzes trade executions.

    Tracks slippage, costs, and latency to validate backtest assumptions.
    """

    def __init__(
        self,
        log_path: str = EXECUTION_LOG_PATH,
        db_path: str = EXECUTION_DB_PATH,
    ):
        self.log_path = Path(log_path)
        self.db_path = Path(db_path)
        self._execution_counter = 0

        # Backtest assumptions for comparison
        self.backtest_assumptions = {
            "base_spread_bps": 200,  # 2%
            "base_slippage_bps": 50,  # 0.5%
            "impact_coef": 0.5,
            "fee_rate_bps": 10,  # 0.1%
            "fill_latency_ms": 500,
        }

        self._init_db()

    def _init_db(self):
        """Initialize SQLite database for execution metrics."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY,
                timestamp TEXT,
                market_id TEXT,
                token_id TEXT,
                side TEXT,
                order_type TEXT,
                requested_size_usd REAL,
                filled_size_usd REAL,
                fill_rate REAL,
                signal_price REAL,
                expected_price REAL,
                actual_price REAL,
                signal_slippage REAL,
                expected_slippage REAL,
                slippage_bps REAL,
                spread_cost REAL,
                impact_cost REAL,
                fees REAL,
                total_cost REAL,
                total_cost_bps REAL,
                signal_time TEXT,
                order_time TEXT,
                fill_time TEXT,
                signal_to_order_ms REAL,
                order_to_fill_ms REAL,
                total_latency_ms REAL,
                bid_price REAL,
                ask_price REAL,
                spread REAL,
                liquidity_estimate REAL,
                signal_source TEXT,
                strategy TEXT,
                notes TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_exec_timestamp
            ON executions(timestamp)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_exec_market
            ON executions(market_id)
        """)

        conn.commit()
        conn.close()

    def _generate_execution_id(self) -> str:
        self._execution_counter += 1
        return f"EXEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._execution_counter:04d}"

    def log_execution(
        self,
        market_id: str,
        token_id: str,
        side: str,
        order_type: str,
        requested_size_usd: float,
        filled_size_usd: float,
        signal_price: float,
        actual_price: float,
        signal_time: datetime,
        order_time: datetime,
        fill_time: datetime,
        bid_price: float,
        ask_price: float,
        fees: float,
        liquidity_estimate: float = 10000,
        signal_source: str = "",
        strategy: str = "",
        notes: str = "",
    ) -> ExecutionRecord:
        """
        Log an execution with full metrics.

        Args:
            market_id: Market identifier
            token_id: Token identifier
            side: "buy" or "sell"
            order_type: "market" or "limit"
            requested_size_usd: Requested order size
            filled_size_usd: Actually filled size
            signal_price: Price when signal was generated
            actual_price: Actual execution price
            signal_time: When signal was generated
            order_time: When order was submitted
            fill_time: When order was filled
            bid_price: Best bid at execution
            ask_price: Best ask at execution
            fees: Fees paid
            liquidity_estimate: Estimated market liquidity
            signal_source: Source of the signal
            strategy: Strategy name
            notes: Additional notes

        Returns:
            ExecutionRecord with calculated metrics
        """
        execution_id = self._generate_execution_id()

        # Calculate metrics
        fill_rate = filled_size_usd / requested_size_usd if requested_size_usd > 0 else 0

        # Slippage calculations
        signal_slippage = (actual_price - signal_price) / signal_price if signal_price > 0 else 0
        if side.lower() == "sell":
            signal_slippage = -signal_slippage  # Negative slippage is good for sells

        # Expected price from backtest model
        spread = ask_price - bid_price
        midpoint = (bid_price + ask_price) / 2

        if side.lower() == "buy":
            expected_price = midpoint * (1 + self.backtest_assumptions["base_slippage_bps"] / 10000)
        else:
            expected_price = midpoint * (1 - self.backtest_assumptions["base_slippage_bps"] / 10000)

        expected_slippage = (actual_price - expected_price) / expected_price if expected_price > 0 else 0

        slippage_bps = signal_slippage * 10000

        # Cost breakdown
        spread_cost = spread / 2 * filled_size_usd  # Half spread
        impact_cost = abs(actual_price - midpoint) * filled_size_usd - spread_cost
        impact_cost = max(0, impact_cost)
        total_cost = spread_cost + impact_cost + fees
        total_cost_bps = (total_cost / filled_size_usd * 10000) if filled_size_usd > 0 else 0

        # Latency
        signal_to_order_ms = (order_time - signal_time).total_seconds() * 1000
        order_to_fill_ms = (fill_time - order_time).total_seconds() * 1000
        total_latency_ms = signal_to_order_ms + order_to_fill_ms

        record = ExecutionRecord(
            execution_id=execution_id,
            timestamp=fill_time,
            market_id=market_id,
            token_id=token_id,
            side=side,
            order_type=order_type,
            requested_size_usd=requested_size_usd,
            filled_size_usd=filled_size_usd,
            fill_rate=fill_rate,
            signal_price=signal_price,
            expected_price=expected_price,
            actual_price=actual_price,
            signal_slippage=signal_slippage,
            expected_slippage=expected_slippage,
            slippage_bps=slippage_bps,
            spread_cost=spread_cost,
            impact_cost=impact_cost,
            fees=fees,
            total_cost=total_cost,
            total_cost_bps=total_cost_bps,
            signal_time=signal_time,
            order_time=order_time,
            fill_time=fill_time,
            signal_to_order_ms=signal_to_order_ms,
            order_to_fill_ms=order_to_fill_ms,
            total_latency_ms=total_latency_ms,
            bid_price=bid_price,
            ask_price=ask_price,
            spread=spread,
            liquidity_estimate=liquidity_estimate,
            signal_source=signal_source,
            strategy=strategy,
            notes=notes,
        )

        # Save to log file
        self._write_log(record)

        # Save to database
        self._write_db(record)

        return record

    def _write_log(self, record: ExecutionRecord):
        """Write record to JSONL log."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as f:
            data = asdict(record)
            # Convert datetimes to strings
            for key in ["timestamp", "signal_time", "order_time", "fill_time"]:
                if isinstance(data[key], datetime):
                    data[key] = data[key].isoformat()
            f.write(json.dumps(data) + "\n")

    def _write_db(self, record: ExecutionRecord):
        """Write record to database."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        data = asdict(record)
        for key in ["timestamp", "signal_time", "order_time", "fill_time"]:
            if isinstance(data[key], datetime):
                data[key] = data[key].isoformat()

        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        values = tuple(data.values())

        cursor.execute(
            f"INSERT OR REPLACE INTO executions ({columns}) VALUES ({placeholders})",
            values
        )
        conn.commit()
        conn.close()

    def get_executions(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        market_id: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> pd.DataFrame:
        """Get executions from database with filters."""
        conn = sqlite3.connect(str(self.db_path))

        query = "SELECT * FROM executions WHERE 1=1"
        params = []

        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date.isoformat())

        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date.isoformat())

        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        query += " ORDER BY timestamp"

        df = pd.read_sql_query(query, conn, params=params)
        conn.close()

        return df

    def calculate_summary(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Optional[ExecutionSummary]:
        """Calculate summary statistics for executions."""
        df = self.get_executions(start_date, end_date)

        if df.empty:
            return None

        # Parse dates
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        return ExecutionSummary(
            period_start=df["timestamp"].min(),
            period_end=df["timestamp"].max(),
            total_executions=len(df),
            total_volume_usd=df["filled_size_usd"].sum(),
            avg_fill_rate=df["fill_rate"].mean(),
            full_fill_pct=(df["fill_rate"] >= 0.99).mean(),
            avg_slippage_bps=df["slippage_bps"].mean(),
            median_slippage_bps=df["slippage_bps"].median(),
            max_slippage_bps=df["slippage_bps"].max(),
            slippage_std_bps=df["slippage_bps"].std(),
            avg_cost_bps=df["total_cost_bps"].mean(),
            total_fees_usd=df["fees"].sum(),
            total_spread_cost_usd=df["spread_cost"].sum(),
            total_impact_cost_usd=df["impact_cost"].sum(),
            avg_latency_ms=df["total_latency_ms"].mean(),
            p50_latency_ms=df["total_latency_ms"].quantile(0.5),
            p95_latency_ms=df["total_latency_ms"].quantile(0.95),
            p99_latency_ms=df["total_latency_ms"].quantile(0.99),
            actual_vs_expected_slippage=df["signal_slippage"].mean() / (self.backtest_assumptions["base_slippage_bps"] / 10000) if self.backtest_assumptions["base_slippage_bps"] > 0 else 1,
            cost_model_accuracy=df[["total_cost_bps", "slippage_bps"]].corr().iloc[0, 1] if len(df) > 1 else 0,
        )

    def compare_to_backtest(self) -> Dict[str, Any]:
        """Compare actual execution to backtest assumptions."""
        summary = self.calculate_summary()

        if summary is None:
            return {"error": "No executions to analyze"}

        comparison = {
            "slippage": {
                "backtest_assumption_bps": self.backtest_assumptions["base_slippage_bps"],
                "actual_avg_bps": summary.avg_slippage_bps,
                "actual_median_bps": summary.median_slippage_bps,
                "ratio": summary.avg_slippage_bps / self.backtest_assumptions["base_slippage_bps"] if self.backtest_assumptions["base_slippage_bps"] > 0 else 0,
                "within_assumption": summary.avg_slippage_bps <= self.backtest_assumptions["base_slippage_bps"],
            },
            "fees": {
                "backtest_assumption_bps": self.backtest_assumptions["fee_rate_bps"],
                "actual_avg_bps": summary.total_fees_usd / summary.total_volume_usd * 10000 if summary.total_volume_usd > 0 else 0,
                "total_fees_usd": summary.total_fees_usd,
            },
            "latency": {
                "backtest_assumption_ms": self.backtest_assumptions["fill_latency_ms"],
                "actual_avg_ms": summary.avg_latency_ms,
                "actual_p95_ms": summary.p95_latency_ms,
                "within_assumption": summary.avg_latency_ms <= self.backtest_assumptions["fill_latency_ms"],
            },
            "fill_rate": {
                "actual_avg": summary.avg_fill_rate,
                "full_fill_pct": summary.full_fill_pct,
            },
            "overall": {
                "model_accurate": (
                    summary.avg_slippage_bps <= self.backtest_assumptions["base_slippage_bps"] * 1.5 and
                    summary.avg_latency_ms <= self.backtest_assumptions["fill_latency_ms"] * 2
                ),
                "recommendation": "",
            }
        }

        # Generate recommendations
        recs = []
        if comparison["slippage"]["ratio"] > 1.5:
            recs.append(f"Slippage is {comparison['slippage']['ratio']:.1f}x higher than assumed. Consider increasing backtest slippage assumption to {summary.avg_slippage_bps:.0f} bps.")
        if comparison["latency"]["actual_p95_ms"] > self.backtest_assumptions["fill_latency_ms"] * 2:
            recs.append(f"P95 latency ({summary.p95_latency_ms:.0f}ms) significantly exceeds assumption. Consider optimizing order routing.")
        if summary.full_fill_pct < 0.9:
            recs.append(f"Only {summary.full_fill_pct:.0%} of orders fully filled. Consider using more aggressive pricing or TWAP.")

        comparison["overall"]["recommendation"] = " ".join(recs) if recs else "Execution quality within acceptable range."

        return comparison

    def generate_report(
        self,
        output_path: Optional[str] = None,
    ) -> str:
        """Generate execution quality report."""
        REPORT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        if output_path is None:
            output_path = REPORT_OUTPUT_DIR / f"execution_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

        summary = self.calculate_summary()
        comparison = self.compare_to_backtest()

        report = []
        report.append("# Execution Quality Report")
        report.append(f"\nGenerated: {datetime.now().isoformat()}")

        if summary is None:
            report.append("\n**No executions to analyze.**")
            report_text = "\n".join(report)
            with open(output_path, "w") as f:
                f.write(report_text)
            return str(output_path)

        report.append("\n## Summary Statistics")
        report.append(f"\n- **Period**: {summary.period_start} to {summary.period_end}")
        report.append(f"- **Total Executions**: {summary.total_executions:,}")
        report.append(f"- **Total Volume**: ${summary.total_volume_usd:,.2f}")
        report.append(f"- **Avg Fill Rate**: {summary.avg_fill_rate:.1%}")
        report.append(f"- **Full Fill Rate**: {summary.full_fill_pct:.1%}")

        report.append("\n## Slippage Analysis")
        report.append(f"\n| Metric | Value |")
        report.append("|--------|-------|")
        report.append(f"| Average | {summary.avg_slippage_bps:.1f} bps |")
        report.append(f"| Median | {summary.median_slippage_bps:.1f} bps |")
        report.append(f"| Maximum | {summary.max_slippage_bps:.1f} bps |")
        report.append(f"| Std Dev | {summary.slippage_std_bps:.1f} bps |")

        report.append("\n## Cost Analysis")
        report.append(f"\n| Cost Type | Total USD | Avg bps |")
        report.append("|-----------|-----------|---------|")
        report.append(f"| Spread | ${summary.total_spread_cost_usd:,.2f} | - |")
        report.append(f"| Impact | ${summary.total_impact_cost_usd:,.2f} | - |")
        report.append(f"| Fees | ${summary.total_fees_usd:,.2f} | - |")
        report.append(f"| **Total** | **${summary.total_spread_cost_usd + summary.total_impact_cost_usd + summary.total_fees_usd:,.2f}** | **{summary.avg_cost_bps:.1f}** |")

        report.append("\n## Latency Analysis")
        report.append(f"\n| Percentile | Latency (ms) |")
        report.append("|------------|--------------|")
        report.append(f"| Average | {summary.avg_latency_ms:.0f} |")
        report.append(f"| P50 | {summary.p50_latency_ms:.0f} |")
        report.append(f"| P95 | {summary.p95_latency_ms:.0f} |")
        report.append(f"| P99 | {summary.p99_latency_ms:.0f} |")

        report.append("\n## Comparison to Backtest Assumptions")

        if "error" not in comparison:
            report.append("\n### Slippage")
            slip = comparison["slippage"]
            status = "✅" if slip["within_assumption"] else "❌"
            report.append(f"\n{status} Assumed: {slip['backtest_assumption_bps']} bps | Actual: {slip['actual_avg_bps']:.1f} bps | Ratio: {slip['ratio']:.2f}x")

            report.append("\n### Latency")
            lat = comparison["latency"]
            status = "✅" if lat["within_assumption"] else "❌"
            report.append(f"\n{status} Assumed: {lat['backtest_assumption_ms']} ms | Actual: {lat['actual_avg_ms']:.0f} ms | P95: {lat['actual_p95_ms']:.0f} ms")

            report.append("\n### Recommendations")
            report.append(f"\n{comparison['overall']['recommendation']}")

        report.append("\n---")
        report.append(f"\n*Report generated by execution logger*")

        report_text = "\n".join(report)
        with open(output_path, "w") as f:
            f.write(report_text)

        print(f"Report saved to: {output_path}")
        return str(output_path)

    def update_backtest_assumptions(self):
        """Update backtest assumptions based on actual execution data."""
        summary = self.calculate_summary()

        if summary is None or summary.total_executions < 10:
            print("Not enough executions to update assumptions")
            return

        # Update with actual values (conservative - use 95th percentile)
        df = self.get_executions()

        self.backtest_assumptions["base_slippage_bps"] = df["slippage_bps"].quantile(0.95)
        self.backtest_assumptions["fill_latency_ms"] = df["total_latency_ms"].quantile(0.95)

        print("Updated backtest assumptions:")
        for key, value in self.backtest_assumptions.items():
            print(f"  {key}: {value}")


def main():
    """Test execution logger."""
    import sys

    logger = ExecutionLogger()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m src.trading.live.execution_logger --report")
        print("  python -m src.trading.live.execution_logger --compare")
        print("  python -m src.trading.live.execution_logger --test")
        return

    command = sys.argv[1]

    if command == "--report":
        logger.generate_report()

    elif command == "--compare":
        comparison = logger.compare_to_backtest()
        print(json.dumps(comparison, indent=2, default=str))

    elif command == "--test":
        print("Logging test executions...")

        now = datetime.now()

        # Log some test executions
        for i in range(5):
            signal_time = now - timedelta(seconds=10)
            order_time = now - timedelta(seconds=5)
            fill_time = now

            record = logger.log_execution(
                market_id=f"test-market-{i}",
                token_id=f"token-{i}",
                side="buy" if i % 2 == 0 else "sell",
                order_type="market",
                requested_size_usd=100 + i * 50,
                filled_size_usd=100 + i * 50,
                signal_price=0.5 + i * 0.01,
                actual_price=0.505 + i * 0.01,
                signal_time=signal_time,
                order_time=order_time,
                fill_time=fill_time,
                bid_price=0.49,
                ask_price=0.51,
                fees=0.5,
                signal_source="test",
                strategy="whale_following",
            )

            print(f"Logged: {record.execution_id} | Slippage: {record.slippage_bps:.1f} bps")

        # Generate report
        logger.generate_report()


if __name__ == "__main__":
    main()
