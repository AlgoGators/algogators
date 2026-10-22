"""Build the daily-report payload (schema_version 1) from a portfolio's detail.

AlgoLens already assembles a portfolio's detail for its own screens (build_strategy_detail:
positions, executions, the equity curve, the finalized lots and the run metrics), so the report is
a different view of the same numbers: nothing is recomputed here and nothing is read from the
database. The weekend and holiday notices are decided here from the date, as trade-ngin's email
did, so the wording is in one place.
"""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from research_api.domain.reports.daily import SCHEMA_VERSION, money, percent, signed_money
from research_api.domain.reports.holidays import HOLIDAYS

DAILY_CHART_DAYS = 30
COMPOSITION_TOP = 7

FOOTNOTES = (
    "This strategy is based on daily OHLCV data. Only the front-month contract is covered.",
    "All values reflect a trading start date of October 5th, 2025.",
    "As of March 1st, 2026 the tradeable universe also includes ZT (2-Year T-Note), ZF (5-Year "
    "T-Note), 6A (Australian Dollar), 6L (Brazilian Real), HO (Heating Oil) and NG (Natural Gas).",
)


def holiday_name(day: date) -> str | None:
    return HOLIDAYS.get(day.isoformat())


def calendar_notices(report_date: date) -> list[dict[str, str]]:
    """Why yesterday's PnL may be missing: it was a weekend day or a market holiday.

    The report goes out on the morning of report_date and describes the day before, as the
    trade-ngin email did: a Sunday report (yesterday Saturday), a Monday report (yesterday Sunday,
    when agricultural futures open in the evening) or any day after a market holiday.
    """
    yesterday = report_date - timedelta(days=1)
    label = yesterday.isoformat()
    follow_up = "Please continue to monitor your positions closely."
    weekday = report_date.weekday()  # Monday is 0
    if weekday == 6:
        return [
            {
                "kind": "weekend",
                "title": "Yesterday was Saturday",
                "body": (
                    f"The latest futures settlement prices are not available, as futures markets were "
                    f"closed yesterday ({label}) due to it being a Saturday. The PnL for these "
                    "contracts will be updated in the next report once settlement data is released."
                ),
                "footnote": follow_up,
            }
        ]
    if weekday == 0:
        return [
            {
                "kind": "weekend",
                "title": "Yesterday was Sunday",
                "body": (
                    f"Agricultural futures settlement prices for Sunday ({label}) are not yet "
                    "available, as these contracts begin trading Sunday evening. The PnL for these "
                    "contracts will be updated in the next report once settlement data is released."
                ),
                "footnote": "Please monitor these positions closely.",
            }
        ]
    name = holiday_name(yesterday)
    if name:
        return [
            {
                "kind": "holiday",
                "title": f"Yesterday was {name}",
                "body": (
                    f"The latest futures settlement prices are not available, as futures markets were "
                    f"closed yesterday ({label}) due to a federal holiday. The PnL for these "
                    "contracts will be updated in the next report once settlement data is released."
                ),
                "footnote": follow_up,
            }
        ]
    return []


def _symbol(name: str) -> str:
    return name.replace(".v.0", "")


def _signed(quantity: float, notional: float) -> float:
    return -abs(notional) if quantity < 0 else abs(notional)


def _positions(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for p in detail["positions"]:
        quantity = float(p["quantity"])
        out.append(
            {
                "symbol": _symbol(p["symbol"]),
                "name": "",
                "quantity": quantity,
                "price": float(p["marketPrice"]),
                "notional": _signed(quantity, float(p["notional"])),
                "weight": float(p["percentOfTotal"]) / 100.0,
            }
        )
    return out


def _executions(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": _symbol(e["symbol"]),
            "side": str(e["side"]),
            "quantity": float(e["quantity"]),
            "price": float(e["price"]),
            "notional": float(e["notional"]),
            "cost": float(e["commission"]),
        }
        for e in detail["executions"]
    ]


def _realized(detail: Mapping[str, Any], report_date: date) -> dict[str, Any]:
    rows = detail["finalizedPositions"]
    if not rows:
        return {}
    return {
        "label": (report_date - timedelta(days=1)).isoformat(),
        "positions": [
            {
                "symbol": r["symbol"],
                "quantity": float(r["quantity"]),
                "entry_price": float(r["entryPrice"]),
                "exit_price": float(r["exitPrice"]),
                "pnl": float(r["realizedPnL"]),
            }
            for r in rows
        ],
    }


def _day(label: str) -> str:
    return label[:10]


def _series(detail: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    curve: list[dict[str, Any]] = [
        {"date": _day(pt["date"]), "value": float(pt["value"])} for pt in detail["historicalData"]
    ]
    equity = curve[-(DAILY_CHART_DAYS + 1) :]
    daily = [
        {"date": cur["date"], "value": round(cur["value"] - prev["value"], 2)}
        for prev, cur in pairwise(equity)
    ]
    by_symbol = [
        {"symbol": r["symbol"], "value": float(r["realizedPnL"])}
        for r in detail["finalizedPositions"]
    ]
    weights = sorted(
        ((_symbol(p["symbol"]), abs(float(p["notional"]))) for p in detail["positions"]),
        key=lambda kv: -kv[1],
    )
    head, tail = weights[:COMPOSITION_TOP], weights[COMPOSITION_TOP:]
    composition = [{"label": s, "value": v} for s, v in head if v > 0]
    if tail and sum(v for _, v in tail) > 0:
        composition.append({"label": "Other", "value": sum(v for _, v in tail)})
    return {
        "equity": equity,
        "daily_pnl": daily,
        "pnl_by_symbol": by_symbol,
        "composition": composition,
    }


def _metrics(detail: Mapping[str, Any]) -> list[dict[str, str]]:
    m = detail["metrics"]
    rows = [
        ("Portfolio value", money(m["currentPortfolioValue"])),
        ("Daily return", f"{m['dailyReturn']:.2f}%"),
        ("Cumulative return", f"{m['cumulativeReturn']:.2f}%"),
        ("Volatility", f"{m['volatility']:.2f}%"),
        ("Sharpe ratio", f"{m['sharpeRatio']:.2f}"),
        ("Max drawdown", f"{m['maxDrawdown']:.2f}%"),
        ("Gross leverage", f"{m['grossLeverage']:.2f}x"),
        ("Net leverage", f"{m['netLeverage']:.2f}x"),
        ("Margin posted", money(m["marginPosted"])),
        ("Margin cushion", f"{m['marginCushion']:.1f}%"),
        ("Total notional", money(m["totalNotional"])),
        ("Transaction costs", money(m["totalCommissions"], 2)),
        ("Net PnL since start", signed_money(m["netPnL"])),
    ]
    return [{"label": label, "value": value} for label, value in rows]


def build_daily_payload(
    detail: Mapping[str, Any],
    portfolio_id: str,
    portfolio_name: str,
    report_date: date,
    rollovers: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """The queue payload for one portfolio's report on report_date."""
    return {
        "schema_version": SCHEMA_VERSION,
        "report_date": report_date.isoformat(),
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio_name,
        "notices": calendar_notices(report_date),
        "positions": _positions(detail),
        "executions": _executions(detail),
        "realized": _realized(detail, report_date),
        "metrics": _metrics(detail),
        "series": _series(detail),
        "rollovers": [dict(r) for r in rollovers],
        "footnotes": list(FOOTNOTES),
    }


__all__ = ["FOOTNOTES", "build_daily_payload", "calendar_notices", "holiday_name", "percent"]
