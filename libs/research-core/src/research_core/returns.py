"""Pure daily-return series statistics.

Extracted from research-api's portfolio calculations so every research member
computes return stats the same way. Stdlib only by design: keep numpy/pandas
out of this module.
"""

from collections.abc import Mapping, Sequence
from typing import Any


def compute_return_stats(historical_data: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Best/worst day, drawdown, win rate, and win/loss aggregates."""
    daily_returns = []
    daily_pnl = []
    for i in range(1, len(historical_data)):
        prev_val = historical_data[i - 1]["value"]
        curr_val = historical_data[i]["value"]
        if prev_val > 0:
            daily_returns.append(((curr_val - prev_val) / prev_val) * 100)
            daily_pnl.append(curr_val - prev_val)

    best_day = max(daily_returns) if daily_returns else 0
    worst_day = min(daily_returns) if daily_returns else 0

    max_drawdown = 0
    peak = historical_data[0]["value"] if historical_data else 0
    for point in historical_data:
        if point["value"] > peak:
            peak = point["value"]
        drawdown = ((peak - point["value"]) / peak) * 100 if peak > 0 else 0
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    winning_days = [pnl for pnl in daily_pnl if pnl > 0]
    losing_days = [pnl for pnl in daily_pnl if pnl < 0]
    total_days = len(daily_pnl)
    win_rate = (len(winning_days) / total_days * 100) if total_days > 0 else 0

    avg_win = sum(winning_days) / len(winning_days) if winning_days else 0
    avg_loss = abs(sum(losing_days) / len(losing_days)) if losing_days else 0

    gross_profit = sum(winning_days) if winning_days else 0
    gross_loss = abs(sum(losing_days)) if losing_days else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0

    return {
        "best_day": best_day,
        "worst_day": worst_day,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
    }


def compute_sharpe(annualized_return: float, volatility: float) -> float:
    return annualized_return / volatility if volatility > 0 else 0
