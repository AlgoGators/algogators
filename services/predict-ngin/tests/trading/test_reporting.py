from datetime import datetime, timedelta

import pandas as pd

from trading.reporting import (
    compute_run_metrics,
    diagnose_trades,
    build_run_summary_from_trades,
    summaries_to_frame,
    diagnostics_to_frame,
)


def _sample_trades() -> pd.DataFrame:
    base = datetime(2026, 1, 1, 12, 0, 0)
    return pd.DataFrame(
        [
            {
                "contract_id": "m1",
                "entry_time": base,
                "exit_time": base + timedelta(days=1),
                "net_pnl": 50.0,
                "gross_pnl": 60.0,
                "position_size": 100.0,
                "status": "closed",
            },
            {
                "contract_id": "m2",
                "entry_time": base + timedelta(days=2),
                "exit_time": base + timedelta(days=3),
                "net_pnl": -20.0,
                "gross_pnl": -10.0,
                "position_size": 100.0,
                "status": "closed",
            },
            {
                "contract_id": "m3",
                "entry_time": base + timedelta(days=4),
                "exit_time": None,
                "net_pnl": 5.0,
                "gross_pnl": 5.0,
                "position_size": 80.0,
                "status": "open",
            },
        ]
    )


def test_compute_run_metrics_basic() -> None:
    trades = _sample_trades()
    metrics = compute_run_metrics(
        trades_df=trades,
        starting_capital=1000,
        position_size=100,
        as_of=pd.Timestamp("2026-01-10"),
    )

    assert metrics.total_trades == 3
    assert metrics.closed_trades == 2
    assert metrics.open_positions == 1
    assert metrics.total_net_pnl == 35.0
    assert metrics.total_costs == 20.0
    assert metrics.win_rate == 0.5


def test_diagnose_trades_flags_issues() -> None:
    trades = _sample_trades().drop(columns=["net_pnl"])
    diagnostics = diagnose_trades(trades)

    assert "missing_columns" in "; ".join(diagnostics.issues)
    assert "net_pnl" in diagnostics.missing_columns


def test_run_summary_exports_frames() -> None:
    trades = _sample_trades()
    summary = build_run_summary_from_trades(
        strategy_name="test",
        trades_df=trades,
        starting_capital=1000,
        position_size=100,
        cost_model="small",
        as_of=pd.Timestamp("2026-01-10"),
    )

    df = summaries_to_frame([summary])
    diag = diagnostics_to_frame([summary])

    assert len(df) == 1
    assert len(diag) == 1
    assert df.loc[0, "strategy_name"] == "test"
