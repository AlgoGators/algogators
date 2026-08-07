"""The "Writeup" stage: render hypothesis + backtest results into a submittable
markdown research writeup, following the same Hypothesis/Data/Methodology/Backtest
structure used throughout the tool.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from algoterminal.data.provider import DataQualityReport
from algoterminal.research.backtest import BacktestResult
from algoterminal.research.models import Hypothesis
from algoterminal.research.storage import ResearchRecord

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "writeup_template.md.txt"


def _data_section(reports: list[DataQualityReport]) -> str:
    if not reports:
        return "No data quality reports available."

    lines = ["| Symbol | Rows | Range | Issues |", "| --- | --- | --- | --- |"]
    for r in reports:
        span = f"{r.start} -> {r.end}" if r.start and r.end else "n/a"
        issues = "; ".join(r.issues) if r.issues else "none"
        lines.append(f"| {r.symbol} | {r.rows} | {span} | {issues} |")
    return "\n".join(lines)


def generate_writeup(
    record: ResearchRecord,
    hypothesis: Hypothesis,
    data_quality: list[DataQualityReport],
    backtest: BacktestResult,
    methodology_notes: str = "",
) -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    stats = backtest.stats

    content = template.format(
        title=hypothesis.title,
        slug=record.slug,
        version=record.version,
        generated_at=datetime.now(timezone.utc).isoformat(),
        thesis=hypothesis.thesis,
        universe=hypothesis.universe,
        symbols=", ".join(hypothesis.symbols),
        expected_edge=hypothesis.expected_edge,
        risk_notes=hypothesis.risk_notes or "None recorded.",
        data_section=_data_section(data_quality),
        methodology_notes=methodology_notes or "No additional methodology notes.",
        cagr=f"{stats.cagr:.2%}",
        sharpe=f"{stats.sharpe:.2f}",
        sortino=f"{stats.sortino:.2f}",
        max_drawdown=f"{stats.max_drawdown:.2%}",
        win_rate=f"{stats.win_rate:.2%}",
        total_return=f"{stats.total_return:.2%}",
        n_trades=stats.n_trades,
    )
    record.writeup_path.write_text(content, encoding="utf-8")
    return content
