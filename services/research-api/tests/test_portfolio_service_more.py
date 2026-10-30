"""Facade tests for portfolio_service: every wrapper delegates to the repository.

The Postgres repository is replaced with an in-memory fake installed via
monkeypatch, so these run without a database and verify the exact arguments
each facade function forwards.
"""

from datetime import datetime
from typing import Any

import pytest
import research_api.services.portfolio_service as svc
from research_api.application.portfolio.ports import PortfolioDetailRows

CURSOR = object()  # opaque token: the facade must pass it through untouched


def make_cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "id": "trendfollowing",
        "name": "Trend Following",
        "description": "Follows trends",
        "initial_equity": 500000.0,
        "managers": ["Ada"],
        "strategy_type": "LIVE_TREND_FOLLOWING",
        "portfolio_id": "MAIN",
    }
    cfg.update(overrides)
    return cfg


def make_latest() -> dict[str, Any]:
    return {
        "current_portfolio_value": 510000.0,
        "volatility": 10.0,
        "total_annualized_return": 20.0,
        "date": datetime(2026, 1, 3),
        "daily_return": 0.5,
        "gross_leverage": 1.5,
        "net_leverage": 1.2,
        "portfolio_leverage": 1.3,
        "margin_posted": 40000.0,
        "equity_to_margin_ratio": 12.75,
        "margin_cushion": 0.8,
        "gross_notional": 750000.0,
        "total_unrealized_pnl": 4000.0,
        "total_realized_pnl": 6000.0,
        "total_transaction_costs": 350.0,
        "cash_available": 100000.0,
    }


class FakeRepo:
    """Stands in for PostgresPortfolioRepository; records every delegated call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.has_type = True
        self.summary: dict[str, Any] | None = make_latest()
        self.detail_rows = PortfolioDetailRows(
            latest=make_latest(),
            equity_curve=[{"timestamp": datetime(2026, 1, 1), "equity": 500000.0}],
            equity_by_stream={},
            positions=[],
            executions=[],
            yesterday_positions=[],
        )

    def _has_portfolio_type(self, cursor: Any) -> bool:
        self.calls.append(("_has_portfolio_type", (cursor,)))
        return self.has_type

    def fetch_portfolio_rows(self, pid: str) -> PortfolioDetailRows:
        self.calls.append(("fetch_portfolio_rows", (pid,)))
        return self.detail_rows

    def fetch_summary_row(self, pid: str) -> dict[str, Any] | None:
        self.calls.append(("fetch_summary_row", (pid,)))
        return self.summary


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    fake = FakeRepo()
    monkeypatch.setattr(svc, "PostgresPortfolioRepository", lambda: fake)
    return fake


# --- thin cursor-level wrappers ----------------------------------------------


def test_has_portfolio_type_delegates(repo: FakeRepo) -> None:
    repo.has_type = False

    assert svc._has_portfolio_type(CURSOR) is False
    assert repo.calls == [("_has_portfolio_type", (CURSOR,))]


# --- strategy detail / summary facades ---------------------------------------


def test_get_strategy_detail_builds_from_repository_rows(repo: FakeRepo) -> None:
    detail = svc.get_strategy_detail(make_cfg())

    assert detail is not None
    assert detail["id"] == "trendfollowing"
    assert detail["invested"] == 500000.0
    assert detail["currentValue"] == 510000.0
    # Issue #59: the book by its portfolio id alone.
    assert repo.calls == [("fetch_portfolio_rows", ("MAIN",))]


def test_get_strategy_detail_returns_none_and_warns_without_rows(
    repo: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    repo.detail_rows = PortfolioDetailRows(
        latest=None,
        equity_curve=[],
        equity_by_stream={},
        positions=[],
        executions=[],
        yesterday_positions=[],
    )

    with caplog.at_level("WARNING", logger="research_api.services.portfolio_service"):
        assert svc.get_strategy_detail(make_cfg()) is None

    assert "No live_results" in caplog.text
    assert "MAIN" in caplog.text


def test_get_strategy_summary_builds_from_latest_row(repo: FakeRepo) -> None:
    summary = svc.get_strategy_summary(make_cfg())

    assert summary is not None
    assert summary["id"] == "trendfollowing"
    assert summary["currentValue"] == 510000.0
    assert summary["returnPercent"] == pytest.approx(2.0)
    assert summary["sharpeRatio"] == pytest.approx(2.0)
    assert repo.calls == [("fetch_summary_row", ("MAIN",))]


def test_get_strategy_summary_returns_none_and_warns_without_row(
    repo: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    repo.summary = None

    with caplog.at_level("WARNING", logger="research_api.services.portfolio_service"):
        assert svc.get_strategy_summary(make_cfg()) is None

    assert "No summary live_results" in caplog.text


# --- facade re-exports -------------------------------------------------------


def test_facade_re_exports_point_at_the_domain_implementations() -> None:
    from research_api.domain.portfolio import calculations as calc
    from research_api.domain.portfolio.streams import PORTFOLIO_STREAMS, PRIMARY_STREAM

    assert svc.PORTFOLIO_STREAMS is PORTFOLIO_STREAMS
    assert svc.PRIMARY_STREAM is PRIMARY_STREAM
    assert svc._build_historical_data is calc.build_historical_data
    assert svc._compute_return_stats is calc.compute_return_stats
    assert svc._f is calc.float_or_default
    assert svc._resolve_initial_equity is calc.resolve_initial_equity
    assert svc._transform_executions is calc.transform_executions
    assert svc._transform_finalized is calc.transform_finalized
    assert svc._transform_positions is calc.transform_positions
