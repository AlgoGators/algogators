"""Built-in report source: paper / forward runs with a performance spec.

Every run from :func:`predict_ngin.viz.paper_sources.discover_runs` that has a ``performance:``
spec in ``config/paper_monitor.local.yaml`` (or a standard ``performance.jsonl``) becomes one
book. The spec's ``group`` column splits the book into strategies; without it the book is one
strategy. A run can set ``portfolio: false`` to stay out of the report, ``capital`` for returns,
and ``strategy_capital: {name: dollars}`` for per-strategy returns.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from predict_ngin.viz import paper_sources as ps
from predict_ngin.viz import performance as perf

from .model import Book, register_source


def book_from_run(run: ps.Run) -> Book | None:
    cfg = run.perf_spec() or {}
    if cfg.get("portfolio") is False:
        return None
    spec = perf.Spec.from_config(cfg) if cfg else perf.standard_spec(run.dir)
    if spec is None:
        return None
    df = perf.load(run.dir, spec)
    if df.empty:
        return None
    groups = (
        df[spec.group].astype(str)
        if spec.group and spec.group in df.columns
        else pd.Series(run.name, index=df.index)
    )
    rows = []
    for g, sub in df.groupby(groups):
        per = perf.period_table(sub, spec, "D")
        if per.empty:
            continue
        t = per.reset_index().rename(columns={"period": "date"})
        t["strategy"] = g
        rows.append(t)
    if not rows:
        return None
    daily = pd.concat(rows, ignore_index=True)
    bench = None
    if spec.benchmark and spec.benchmark in df.columns:
        b = df.set_index("_t")[spec.benchmark].astype(float).resample("D").last().dropna()
        bench = b if len(b) else None
    note = ""
    if spec.kind != "pnl" or not spec.costs_in_pnl:
        note = f"{spec.kind}; costs {'in' if spec.costs_in_pnl else 'subtracted from'} P&L"
    if cfg.get("note"):
        note = str(cfg["note"])
    return Book(
        name=run.name,
        daily=daily[
            ["date", "strategy", "net", "gross", *[c for c in spec.costs if c in daily.columns]]
        ],
        capital=spec.capital,
        cost_cols=[c for c in spec.costs if c in daily.columns],
        benchmark=bench,
        strategy_capital={str(k): float(v) for k, v in (cfg.get("strategy_capital") or {}).items()},
        source="paper_runs",
        note=note,
    )


@register_source("paper_runs")
def paper_runs() -> List[Book]:
    out = []
    for r in ps.discover_runs():
        b = book_from_run(r)
        if b is not None:
            out.append(b)
    return out
