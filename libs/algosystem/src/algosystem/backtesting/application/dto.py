"""Application-layer request and response DTOs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from algosystem.backtesting.domain.backtest import BacktestResult
from algosystem.shared.values import DateRange, Money, Percent, RunId

PriceInput = pd.DataFrame | pd.Series


@dataclass(frozen=True)
class RunBacktestRequest:
    """Request to run a backtest from raw price/equity inputs."""

    data: PriceInput
    benchmark: PriceInput | None = None
    start: object | None = None
    end: object | None = None
    initial_capital: object | None = None
    price_column: str | None = None


@dataclass(frozen=True)
class RunBacktestResponse:
    """Result of running a backtest, mapped to caller-friendly values."""

    run_id: RunId | None
    metrics: dict[str, float]
    equity: pd.Series
    benchmark: pd.Series | None
    date_range: DateRange
    initial_capital: Money
    final_capital: Money
    total_return: Percent
    summary: dict[str, object]


@dataclass(frozen=True)
class ArchiveRunRequest:
    """Request to persist a completed backtest result."""

    result: BacktestResult
    name: str | None = None
    description: str = ""
    hyperparameters: Mapping[str, object] | None = None
    overwrite: bool = False


@dataclass(frozen=True)
class ArchiveRunResponse:
    """Response returned after a run has been archived."""

    run_id: RunId


@dataclass(frozen=True)
class LoadRunRequest:
    """Request to load a persisted run."""

    run_id: RunId | str


@dataclass(frozen=True)
class LoadRunResponse:
    """Response containing a rehydrated backtest result."""

    result: BacktestResult


@dataclass(frozen=True)
class CompareRunsRequest:
    """Request to compare persisted runs."""

    run_ids: Sequence[RunId | str]


@dataclass(frozen=True)
class CompareRunsResponse:
    """Response containing summaries and aligned equity curves."""

    summaries: list[dict[str, object]]
    equity_curves: pd.DataFrame


@dataclass(frozen=True)
class GenerateTearsheetRequest:
    """Request to render a quantstats tearsheet for a result."""

    result: BacktestResult
    output: Path | str
    title: str
    mode: str = "html"
    rf: float = 0.0
    periods_per_year: int = 252


@dataclass(frozen=True)
class GenerateTearsheetResponse:
    """Response returned after rendering a tearsheet."""

    output: Path
