"""Portfolio use-case tests: builders and orchestrators with fake ports (no DB)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

import pytest
from research_api.application.portfolio.ports import (
    IncubationError,
    IncubationPerformanceRows,
    PortfolioDetailRows,
    PortfolioReaderPort,
    StrategyRegistryPort,
)
from research_api.application.portfolio.use_cases import (
    GetIncubationPerformance,
    GetStrategyDetail,
    ListIncubatingStrategies,
    ListStrategies,
    PromoteToLive,
    RetireStrategy,
    StartIncubation,
    StrategyDataNotFoundError,
    StrategyNotFoundError,
    build_incubating_strategy,
    build_incubation_performance,
    build_strategy_detail,
    build_strategy_summary,
)

# --- shared row builders -----------------------------------------------------


def make_cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "id": "trendfollowing",
        "name": "Trend Following",
        "description": "Follows trends",
        "initial_equity": 500000.0,
        "managers": ["Ada", "Grace"],
        "strategy_type": "LIVE_TREND_FOLLOWING",
        "portfolio_id": "MAIN",
    }
    cfg.update(overrides)
    return cfg


def make_latest(**overrides: Any) -> dict[str, Any]:
    latest: dict[str, Any] = {
        "current_portfolio_value": 510000.0,
        "volatility": 10.0,
        "total_annualized_return": 20.0,
        "date": datetime(2026, 1, 3, 16, 30),
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
    latest.update(overrides)
    return latest


def make_detail_rows(**overrides: Any) -> PortfolioDetailRows:
    equity_curve = [
        {"timestamp": datetime(2026, 1, 1), "equity": 500000.0},
        {"timestamp": datetime(2026, 1, 2), "equity": 505000.0},
        {"timestamp": datetime(2026, 1, 3), "equity": 510000.0},
    ]
    fields: dict[str, Any] = {
        "latest": make_latest(),
        "equity_curve": equity_curve,
        "equity_by_stream": {"qt": equity_curve[-1:]},
        "positions": [{"symbol": "ES.v.0", "quantity": 2.0, "average_price": 100.0}],
        "executions": [
            {
                "symbol": "ES.v.0",
                "side": "BUY",
                "quantity": 2.0,
                "price": 100.0,
                "execution_time": datetime(2026, 1, 2, 9, 31),
                "commissions_fees": 2.5,
            }
        ],
        "yesterday_positions": [
            {"symbol": "ES.v.0", "quantity": 2.0, "average_price": 100.0},
            {
                "symbol": "NQ.v.0",
                "quantity": 1.0,
                "average_price": 200.0,
                "daily_realized_pnl": 7.5,
            },
        ],
    }
    fields.update(overrides)
    return PortfolioDetailRows(**fields)


class FakeReader:
    """PortfolioReaderPort recording every call, with canned responses."""

    def __init__(
        self,
        summary_rows: dict[str, Mapping[str, Any] | None] | None = None,
        detail_rows: PortfolioDetailRows | None = None,
        incubating: Sequence[Mapping[str, Any]] = (),
        performance: IncubationPerformanceRows | None = None,
    ):
        self.summary_rows = summary_rows or {}
        self.detail_rows = detail_rows
        self.incubating = incubating
        self.performance = performance or IncubationPerformanceRows(positions=[], equity_curve=[])
        self.calls: list[tuple[str, Any]] = []

    def fetch_summary_row(self, strategy_type: str, portfolio_id: str) -> Mapping[str, Any] | None:
        self.calls.append(("fetch_summary_row", (strategy_type, portfolio_id)))
        if strategy_type not in self.summary_rows:
            raise RuntimeError(f"database exploded for {strategy_type}")
        return self.summary_rows[strategy_type]

    def fetch_detail_rows(self, strategy_type: str, portfolio_id: str) -> PortfolioDetailRows:
        self.calls.append(("fetch_detail_rows", (strategy_type, portfolio_id)))
        assert self.detail_rows is not None
        return self.detail_rows

    def list_incubating_strategies(self) -> Sequence[Mapping[str, Any]]:
        return self.incubating

    def fetch_incubation_performance(self, strategy_id: str) -> IncubationPerformanceRows:
        self.calls.append(("fetch_incubation_performance", strategy_id))
        return self.performance

    def start_incubation(
        self, strategy_id: str, mock_capital: float, reason: str, user_id: str
    ) -> None:
        self.calls.append(
            ("start_incubation", (strategy_id, mock_capital, reason, user_id)),
        )

    def promote_to_live(self, strategy_id: str, reason: str, user_id: str) -> None:
        self.calls.append(("promote_to_live", (strategy_id, reason, user_id)))

    def retire_strategy(self, strategy_id: str, reason: str, user_id: str) -> None:
        self.calls.append(("retire_strategy", (strategy_id, reason, user_id)))


class FakeRegistry:
    def __init__(self, configs: Sequence[dict[str, Any]]):
        self.configs = list(configs)

    def list(self, active_only: bool = True) -> list[dict[str, Any]]:
        return self.configs

    def get(self, strategy_id: str) -> dict[str, Any] | None:
        return next((c for c in self.configs if c["id"] == strategy_id), None)


def reader_port(reader: FakeReader) -> PortfolioReaderPort:
    return cast(PortfolioReaderPort, reader)


def registry_port(registry: FakeRegistry) -> StrategyRegistryPort:
    return cast(StrategyRegistryPort, registry)


# --- build_strategy_detail ---------------------------------------------------


def test_build_strategy_detail_returns_none_without_latest_row():
    rows = make_detail_rows(latest=None)

    assert build_strategy_detail(make_cfg(), rows) is None


def test_build_strategy_detail_happy_path():
    detail = build_strategy_detail(make_cfg(), make_detail_rows())

    assert detail is not None
    assert detail["id"] == "trendfollowing"
    assert detail["name"] == "Trend Following"
    assert detail["invested"] == 500000.0  # first equity point, not cfg base
    assert detail["currentValue"] == 510000.0
    assert detail["return"] == 10000.0
    assert detail["returnPercent"] == pytest.approx(2.0)
    assert detail["managers"] == ["Ada", "Grace"]
    assert detail["lastUpdate"] == "2026-01-03T16:30:00"

    assert detail["historicalData"] == [
        {"date": "2026-01-01T00:00:00", "value": 500000.0},
        {"date": "2026-01-02T00:00:00", "value": 505000.0},
        {"date": "2026-01-03T00:00:00", "value": 510000.0},
    ]
    assert detail["equityByStream"] == {"qt": [{"date": "2026-01-03T00:00:00", "value": 510000.0}]}

    assert detail["bestDay"] == pytest.approx(1.0)
    assert detail["worstDay"] == pytest.approx(5000 / 505000 * 100)

    [position] = detail["positions"]
    assert position["name"] == "ES"
    assert position["notional"] == 200.0
    assert position["percentOfTotal"] == pytest.approx(200.0 / 510000.0 * 100)

    [execution] = detail["executions"]
    assert execution["notional"] == 200.0
    assert execution["date"] == "2026-01-02T09:31:00"

    # ES is unchanged day-over-day (skipped); NQ went 1 -> 0 (closed).
    [finalized] = detail["finalizedPositions"]
    assert finalized["symbol"] == "NQ"
    assert finalized["exitPrice"] == 200.0
    assert finalized["realizedPnL"] == 7.5

    metrics = detail["metrics"]
    assert metrics["sharpeRatio"] == pytest.approx(2.0)  # 20 / 10
    assert metrics["totalTrades"] == 1
    assert metrics["winRate"] == 100.0
    assert metrics["maxDrawdown"] == 0
    assert metrics["avgWin"] == 5000.0
    assert metrics["avgLoss"] == 0
    assert metrics["profitFactor"] == 0  # no losing days
    assert metrics["netPnL"] == 10000.0
    assert metrics["cumulativeReturn"] == pytest.approx(2.0)
    assert metrics["grossLeverage"] == 1.5
    assert metrics["cashAvailable"] == 100000.0


def test_build_strategy_detail_degenerate_inputs_do_not_divide_by_zero():
    rows = make_detail_rows(
        latest=make_latest(volatility=0.0, daily_return=None, gross_leverage=None),
        equity_curve=[],
        equity_by_stream={},
        positions=[],
        executions=[],
        yesterday_positions=[],
    )
    cfg = make_cfg(initial_equity=0.0)

    detail = build_strategy_detail(cfg, rows)

    assert detail is not None
    assert detail["invested"] == 0.0
    assert detail["returnPercent"] == 0  # zero base equity guarded
    assert detail["historicalData"] == []
    assert detail["metrics"]["sharpeRatio"] == 0  # zero volatility guarded
    assert detail["metrics"]["dailyReturn"] == 0  # None coerced to default
    assert detail["metrics"]["grossLeverage"] == 0
    assert detail["metrics"]["totalTrades"] == 0


# --- build_strategy_summary --------------------------------------------------


def test_build_strategy_summary_returns_none_without_latest():
    assert build_strategy_summary(make_cfg(), None) is None


def test_build_strategy_summary_happy_path():
    summary = build_strategy_summary(make_cfg(), make_latest())

    assert summary == {
        "id": "trendfollowing",
        "name": "Trend Following",
        "currentValue": 510000.0,
        "returnPercent": pytest.approx(2.0),
        "volatility": 10.0,
        "sharpeRatio": pytest.approx(2.0),
        "annualizedReturn": 20.0,
    }


def test_build_strategy_summary_guards_zero_base_equity_and_volatility():
    summary = build_strategy_summary(make_cfg(initial_equity=0.0), make_latest(volatility=0.0))

    assert summary is not None
    assert summary["returnPercent"] == 0
    assert summary["sharpeRatio"] == 0


# --- incubation builders -----------------------------------------------------


def test_build_incubating_strategy_full_row():
    started = datetime(2026, 1, 1)
    now = datetime(2026, 1, 31)

    built = build_incubating_strategy(
        {
            "id": "meanrev",
            "strategy_type": "LIVE_MEAN_REV",
            "portfolio_id": "MOCK",
            "name": "Mean Reversion",
            "description": "Reverts",
            "mock_capital": 250000,
            "incubation_started_at": started,
        },
        now,
    )

    assert built["id"] == "meanrev"
    assert built["description"] == "Reverts"
    assert built["mock_capital"] == 250000.0
    assert built["incubation_started_at"] is started
    assert built["days_elapsed"] == 30
    assert built["window_days"] == 120
    assert built["progress"] == pytest.approx(25.0)


def test_build_incubating_strategy_defaults_optional_fields():
    built = build_incubating_strategy(
        {
            "id": "meanrev",
            "strategy_type": "LIVE_MEAN_REV",
            "portfolio_id": "MOCK",
            "name": "Mean Reversion",
            "description": None,
            "mock_capital": None,
        },
        datetime(2026, 1, 31),
    )

    assert built["description"] == ""
    assert built["mock_capital"] is None
    assert built["incubation_started_at"] is None
    assert built["days_elapsed"] == 0
    assert built["progress"] == 0


def test_build_incubation_performance_copies_rows_into_lists():
    positions = ({"symbol": "ES"},)
    equity_curve = ({"equity": 1.0},)

    result = build_incubation_performance(
        IncubationPerformanceRows(positions=positions, equity_curve=equity_curve)
    )

    assert result == {"positions": [{"symbol": "ES"}], "equity_curve": [{"equity": 1.0}]}
    assert isinstance(result["positions"], list)


# --- orchestrating use cases -------------------------------------------------


def test_get_strategy_detail_unknown_id_raises_not_found():
    registry = FakeRegistry([])
    reader = FakeReader()

    with pytest.raises(StrategyNotFoundError):
        GetStrategyDetail(registry_port(registry), reader_port(reader)).execute("nope")

    assert reader.calls == []


def test_get_strategy_detail_known_id_without_data_raises_data_not_found():
    registry = FakeRegistry([make_cfg()])
    reader = FakeReader(detail_rows=make_detail_rows(latest=None))

    with pytest.raises(StrategyDataNotFoundError):
        GetStrategyDetail(registry_port(registry), reader_port(reader)).execute("trendfollowing")


def test_get_strategy_detail_happy_path_queries_by_type_and_portfolio():
    registry = FakeRegistry([make_cfg()])
    reader = FakeReader(detail_rows=make_detail_rows())

    detail = GetStrategyDetail(registry_port(registry), reader_port(reader)).execute(
        "trendfollowing"
    )

    assert detail["id"] == "trendfollowing"
    assert reader.calls == [("fetch_detail_rows", ("LIVE_TREND_FOLLOWING", "MAIN"))]


def test_list_strategies_skips_failures_and_missing_rows(caplog):
    good = make_cfg()
    no_data = make_cfg(id="empty", strategy_type="LIVE_EMPTY")
    broken = make_cfg(id="broken", strategy_type="LIVE_BROKEN")
    reader = FakeReader(
        summary_rows={
            "LIVE_TREND_FOLLOWING": make_latest(),
            "LIVE_EMPTY": None,
            # LIVE_BROKEN intentionally absent -> fetch_summary_row raises
        }
    )

    with caplog.at_level("ERROR"):
        result = ListStrategies(
            registry_port(FakeRegistry([good, no_data, broken])), reader_port(reader)
        ).execute()

    assert [s["id"] for s in result] == ["trendfollowing"]
    assert "broken" in caplog.text


def test_list_incubating_strategies_builds_each_row():
    reader = FakeReader(
        incubating=[
            {
                "id": "meanrev",
                "strategy_type": "LIVE_MEAN_REV",
                "portfolio_id": "MOCK",
                "name": "Mean Reversion",
                "description": None,
                "mock_capital": 100000,
                "incubation_started_at": datetime(2026, 1, 1),
            }
        ]
    )

    result = ListIncubatingStrategies(reader_port(reader)).execute(datetime(2026, 1, 13))

    assert [(row["id"], row["days_elapsed"]) for row in result] == [("meanrev", 12)]


def test_get_incubation_performance_delegates_strategy_id():
    reader = FakeReader(
        performance=IncubationPerformanceRows(
            positions=[{"symbol": "ES"}], equity_curve=[{"equity": 5.0}]
        )
    )

    result = GetIncubationPerformance(reader_port(reader)).execute("meanrev")

    assert reader.calls == [("fetch_incubation_performance", "meanrev")]
    assert result["positions"] == [{"symbol": "ES"}]
    assert result["equity_curve"] == [{"equity": 5.0}]


def test_start_incubation_strips_reason_and_forwards_arguments():
    reader = FakeReader()

    StartIncubation(reader_port(reader)).execute(
        strategy_id="meanrev",
        mock_capital=250000.0,
        reason="  looks promising  ",
        user_id="7",
    )

    assert reader.calls == [("start_incubation", ("meanrev", 250000.0, "looks promising", "7"))]


def test_start_incubation_rejects_non_positive_capital_before_reader():
    reader = FakeReader()

    with pytest.raises(IncubationError, match="mock_capital"):
        StartIncubation(reader_port(reader)).execute(
            strategy_id="meanrev", mock_capital=-1.0, reason="fine", user_id="7"
        )

    assert reader.calls == []


def test_promote_to_live_forwards_stripped_reason():
    reader = FakeReader()

    PromoteToLive(reader_port(reader)).execute("meanrev", " earned it ", "7")

    assert reader.calls == [("promote_to_live", ("meanrev", "earned it", "7"))]


def test_retire_strategy_requires_reason():
    reader = FakeReader()

    with pytest.raises(IncubationError, match="reason"):
        RetireStrategy(reader_port(reader)).execute("meanrev", "", "7")

    assert reader.calls == []


def test_retire_strategy_forwards_arguments():
    reader = FakeReader()

    RetireStrategy(reader_port(reader)).execute("meanrev", "underperformed", "7")

    assert reader.calls == [("retire_strategy", ("meanrev", "underperformed", "7"))]
