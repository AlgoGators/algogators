"""Paper-runner base, on top of ``predict_ngin.capture.runner`` (D12, D13).

A paper runner is a one-pass scheduled job (``run_one_pass``: STOP file, lock, exit codes) that
keeps a JSON state between passes, appends what it decided to JSON-lines journals, writes a
``status.json`` with ``mode`` "paper", and reports its P&L against a cash benchmark. This module
holds only those shared pieces; each runner keeps its own strategy and accounting.

Formats are the ones ``scripts/live/perps_carry_paper.py`` already wrote, so its files are
unchanged: state and status are written atomically with ``indent=1, default=str``; journal rows
are one ``json.dumps(row, default=str)`` per line.

PAPER ONLY: nothing here places, amends or cancels orders.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from predict_ngin.capture.runner import atomic_write_json, run_one_pass, status_fields

__all__ = [
    "PAPER_MODE",
    "run_one_pass",
    "paper_status",
    "write_json",
    "load_state",
    "append_jsonl",
    "accrue_benchmark",
    "excess_vs_benchmark",
    "append_performance",
]

PAPER_MODE = "paper"


def paper_status(name: str, ok: bool, last_run: str, **extra) -> Dict[str, Any]:
    """``status.json`` document of a paper runner: ``status_fields`` with ``mode="paper"``."""
    return status_fields(name, PAPER_MODE, ok, last_run, **extra)


def write_json(path, obj: Any) -> None:
    """Atomic state/status write in the paper format (``indent=1, default=str``)."""
    atomic_write_json(path, obj, indent=1, default=str)


def load_state(path, new: Callable[[], dict]) -> dict:
    """The saved state at ``path``, or ``new()`` on the first pass."""
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    return new()


def append_jsonl(path, rows: Iterable[Mapping]) -> None:
    """Append rows to a JSON-lines journal (decisions, events, snapshots); no-op when empty."""
    rows = list(rows)
    if rows:
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + "\n")


def accrue_benchmark(value: float, rate: float, dt_years: float) -> float:
    """Cash benchmark after ``dt_years`` at simple ``rate`` per year (compounded per pass)."""
    return value * (1 + rate * dt_years)


def excess_vs_benchmark(equity: Mapping[str, float], benchmark: float) -> Dict[str, float]:
    """P&L versus the benchmark for each equity scenario (keys kept in order)."""
    return {k: v - benchmark for k, v in equity.items()}


def append_performance(run_dir, ts: str, equity: float, benchmark: Optional[float] = None,
                       costs: Optional[Mapping[str, float]] = None, **extra) -> None:
    """Append one row to ``<run_dir>/performance.jsonl``: the standard series the Paper trading
    page reads (equity curve, returns, Sharpe, drawdown, costs). Call once per pass.

    ``equity`` is the account value now; ``benchmark`` the value of the cash benchmark;
    ``costs`` cumulative costs by kind (fees, slippage, ...); ``extra`` any other numbers
    (e.g. equity under other cost scenarios). An extra ``capital`` (the starting capital) lets the
    report compute returns.
    """
    row = {"ts": ts, "equity": float(equity)}
    if benchmark is not None:
        row["benchmark"] = float(benchmark)
    for k, v in (costs or {}).items():
        row[f"cum_cost_{k}"] = float(v)
    row.update(extra)
    append_jsonl(Path(run_dir) / "performance.jsonl", [row])
