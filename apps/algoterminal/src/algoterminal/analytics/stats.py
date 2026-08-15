"""Shared return/risk statistics used by both the backtest engine and the
fund performance dashboard, so the two report numbers the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


@dataclass
class PerformanceStats:
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    total_return: float
    win_rate: float | None = None
    n_trades: int | None = None


def drawdown_series(equity: pd.Series) -> pd.Series:
    running_max = equity.cummax()
    return (equity - running_max) / running_max


def rolling_sharpe(returns: pd.Series, window: int = 63) -> pd.Series:
    """Rolling annualized Sharpe over a trailing window (default ~1 quarter of trading days)."""
    rolling_mean = returns.rolling(window).mean()
    rolling_std = returns.rolling(window).std()
    sharpe = (rolling_mean / rolling_std) * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe.name = f"Rolling Sharpe ({window}d)"
    return sharpe.dropna()


def monthly_returns_table(returns: pd.Series) -> pd.DataFrame:
    """Calendar table of monthly returns: rows are years, columns Jan..Dec plus a Year total."""
    import calendar

    frame = returns.to_frame("ret")
    frame["year"] = frame.index.year
    frame["month"] = frame.index.month

    monthly = (
        frame.groupby(["year", "month"])["ret"].apply(lambda r: (1 + r).prod() - 1).unstack("month")
    )
    monthly = monthly.rename(columns=lambda m: calendar.month_abbr[m])
    monthly = monthly.reindex(columns=[calendar.month_abbr[m] for m in range(1, 13)])

    yearly = frame.groupby("year")["ret"].apply(lambda r: (1 + r).prod() - 1)
    monthly["Year"] = yearly
    return monthly


def drawdown_periods(equity: pd.Series, top_n: int = 5) -> pd.DataFrame:
    """The `top_n` deepest drawdown episodes: start (prior peak), trough, end,
    depth, length in days, and whether the drawdown has recovered.
    """
    records: list[dict] = []
    in_drawdown = False
    peak_date = equity.index[0]
    peak_value = float(equity.iloc[0])
    trough_date = None
    trough_value = None

    for current_date, value in equity.items():
        value = float(value)
        if value >= peak_value:
            # trough_value is always set while in a drawdown; the explicit
            # None check just narrows the Optional for the type checker.
            if in_drawdown and trough_value is not None:
                records.append(
                    {
                        "start": peak_date,
                        "trough": trough_date,
                        "end": current_date,
                        "depth": (trough_value - peak_value) / peak_value,
                        "length_days": (current_date - peak_date).days,
                        "recovered": True,
                    }
                )
                in_drawdown = False
            peak_date, peak_value = current_date, value
            trough_date, trough_value = None, None
        else:
            in_drawdown = True
            if trough_value is None or value < trough_value:
                trough_date, trough_value = current_date, value

    if in_drawdown and trough_value is not None:
        records.append(
            {
                "start": peak_date,
                "trough": trough_date,
                "end": equity.index[-1],
                "depth": (trough_value - peak_value) / peak_value,
                "length_days": (equity.index[-1] - peak_date).days,
                "recovered": False,
            }
        )

    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    return frame.sort_values("depth").head(top_n).reset_index(drop=True)


def performance_stats(
    returns: pd.Series,
    equity: pd.Series,
    positions: pd.Series | None = None,
) -> PerformanceStats:
    n_periods = len(returns)
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1) if n_periods else 0.0

    years = n_periods / TRADING_DAYS_PER_YEAR if n_periods else 0.0
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0

    daily_std = returns.std()
    sharpe = (
        float(returns.mean() / daily_std * np.sqrt(TRADING_DAYS_PER_YEAR)) if daily_std else 0.0
    )

    downside_std = returns[returns < 0].std()
    sortino = (
        float(returns.mean() / downside_std * np.sqrt(TRADING_DAYS_PER_YEAR))
        if downside_std
        else 0.0
    )

    max_dd = float(drawdown_series(equity).min())

    win_rate = None
    n_trades = None
    if positions is not None:
        active = returns[positions.shift(1).fillna(0) != 0]
        win_rate = float((active > 0).mean()) if len(active) else 0.0
        n_trades = int(
            (positions.diff().fillna(positions.iloc[0] if len(positions) else 0) != 0).sum()
        )

    return PerformanceStats(
        cagr=cagr,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        total_return=total_return,
        win_rate=win_rate,
        n_trades=n_trades,
    )
