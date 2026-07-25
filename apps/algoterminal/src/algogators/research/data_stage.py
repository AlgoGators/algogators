"""The "Data" stage: pull and validate the data a hypothesis needs."""

from __future__ import annotations

from datetime import date

import pandas as pd
import yaml

from algogators.data.provider import DataProvider, DataQualityReport
from algogators.research.models import Hypothesis
from algogators.research.storage import ResearchRecord


def pull_and_validate(
    hypothesis: Hypothesis,
    provider: DataProvider,
    start: date | None = None,
    end: date | None = None,
) -> tuple[dict[str, pd.DataFrame], list[DataQualityReport]]:
    """Pull data for every symbol in the hypothesis and flag basic quality issues."""
    data = provider.fetch_many(hypothesis.symbols, hypothesis.asset_class, start, end)
    reports = [DataProvider.quality_report(sym, df) for sym, df in data.items()]
    return data, reports


def save_quality_reports(record: ResearchRecord, reports: list[DataQualityReport]) -> None:
    payload = [
        {
            "symbol": r.symbol,
            "rows": r.rows,
            "missing_values": r.missing_values,
            "start": str(r.start) if r.start else None,
            "end": str(r.end) if r.end else None,
            "issues": r.issues,
            "ok": r.ok,
        }
        for r in reports
    ]
    with open(record.data_quality_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)


def load_quality_reports(record: ResearchRecord) -> list[DataQualityReport]:
    from datetime import date as _date

    if not record.data_quality_path.exists():
        return []
    with open(record.data_quality_path, encoding="utf-8") as f:
        payload = yaml.safe_load(f) or []

    def _parse_date(value: str | None):
        return _date.fromisoformat(value) if value else None

    return [
        DataQualityReport(
            symbol=item["symbol"],
            rows=item["rows"],
            missing_values=item["missing_values"],
            start=_parse_date(item.get("start")),
            end=_parse_date(item.get("end")),
            issues=list(item.get("issues", [])),
        )
        for item in payload
    ]
