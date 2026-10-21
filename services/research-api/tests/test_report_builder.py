"""The daily-report builder: a portfolio detail in, a valid schema_version 1 payload out."""

from datetime import date

from research_api.application.portfolio.ports import PortfolioDetailRows
from research_api.application.portfolio.use_cases import build_strategy_detail
from research_api.domain.reports.builder import build_daily_payload, calendar_notices
from research_api.domain.reports.daily import parse_daily_report
from research_api.infrastructure.reports.renderer import HtmlReportRenderer


def _detail():
    from datetime import datetime

    latest = {
        "current_portfolio_value": 510000.0,
        "volatility": 10.0,
        "total_annualized_return": 20.0,
        "date": datetime(2026, 10, 21, 16, 30),
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
    curve = [
        {"timestamp": datetime(2026, 10, d), "equity": 500000.0 + 1000 * d} for d in range(1, 21)
    ]
    rows = PortfolioDetailRows(
        latest=latest,
        equity_curve=curve,
        equity_by_stream={},
        positions=[
            {"symbol": "ES.v.0", "quantity": 2, "average_price": 5400.0},
            {"symbol": "CL.v.0", "quantity": -1, "average_price": 68.0},
        ],
        executions=[
            {
                "symbol": "ES.v.0",
                "side": "BUY",
                "quantity": 1,
                "price": 5400.0,
                "commissions_fees": 2.1,
                "execution_time": datetime(2026, 10, 21, 9, 30),
            }
        ],
        yesterday_positions=[
            {
                "symbol": "NQ.v.0",
                "quantity": 1,
                "average_price": 19000.0,
                "daily_realized_pnl": 120.0,
            }
        ],
    )
    cfg = {
        "id": "qt",
        "name": "Trend Following",
        "description": "",
        "initial_equity": 500000.0,
        "managers": ["x"],
        "portfolio_id": "P",
        "strategy_type": "S",
    }
    return build_strategy_detail(cfg, rows)


def test_a_portfolio_detail_becomes_a_payload_the_report_accepts():
    payload = build_daily_payload(_detail(), "P", "Trend Following", date(2026, 10, 21))
    report = parse_daily_report(payload)
    assert [p.symbol for p in report.positions] == ["ES", "CL"]
    assert report.positions[1].notional < 0  # a short stays signed
    assert report.executions[0].cost == 2.1
    assert report.total_realized_pnl == 120.0
    assert len(report.daily_pnl) == len(report.equity) - 1
    assert sum(w.value for w in [type("W", (), {"value": v})() for _, v in report.composition]) > 0


def test_the_built_payload_renders():
    payload = build_daily_payload(_detail(), "P", "Trend Following", date(2026, 10, 21))
    email = HtmlReportRenderer().render_daily(parse_daily_report(payload))
    assert "Daily Trading Report" in email.html and "equity" in email.images


def test_calendar_notices_follow_the_weekday_and_the_holiday_table():
    assert calendar_notices(date(2026, 10, 25))[0]["title"] == "Yesterday was Saturday"  # Sunday
    assert calendar_notices(date(2026, 10, 26))[0]["title"] == "Yesterday was Sunday"  # Monday
    assert calendar_notices(date(2026, 10, 21)) == []  # Wednesday
    assert calendar_notices(date(2025, 12, 26))[0]["title"] == "Yesterday was Christmas Day"
