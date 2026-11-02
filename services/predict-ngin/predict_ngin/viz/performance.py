"""Performance of a paper/forward run from any table it writes (no Streamlit; unit-tested).

A :class:`Spec` says how to read P&L out of a runner's files: which file(s) (a glob), the time
column(s), the P&L columns (summed per row), cost columns (fees, slippage, ...), whether the P&L
is per row or an equity level, optional row filters and a grouping column (rule, model,
strategy ...). Specs come from ``config/paper_monitor.local.yaml`` (``performance:`` under a
run), from the standard ``performance.jsonl`` a runner can append with
:func:`predict_ngin.paper.runner.append_performance`, or from the page's pickers.

:func:`period_table` buckets the rows into hourly / daily / weekly periods; :func:`metrics`
turns that into the usual numbers. Returns are period P&L over the equity at the start of the
period (``capital`` plus P&L so far). Annualisation: 8760 periods a year for hours, 365 for days
(markets trade every day), 52 for weeks. Sharpe and PSR use :mod:`predict_ngin.evaluation`.
With fewer than ~30 periods every ratio here is noise; the page says so.
"""

from __future__ import annotations

import glob
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from predict_ngin.evaluation import probabilistic_sharpe, sharpe

PERIODS = {"h": 8760.0, "D": 365.0, "W": 52.0}
PERF_FILE = "performance.jsonl"
MIN_DAYS_ANNUALISE = 7.0
COST_WORDS = ("fee", "cost", "slip", "spread", "commission", "impact", "gas")


@dataclass
class Spec:
    file: str  # glob relative to the run folder, e.g. "days/*.parquet"
    time: List[str]  # first non-null of these columns per row
    pnl: List[str]  # summed per row (or an equity level when kind == "equity")
    costs: List[str] = field(default_factory=list)
    kind: str = "pnl"  # "pnl" (per row) | "cum_pnl" | "equity"
    costs_in_pnl: bool = True  # True: pnl is already net of the cost columns
    where: Dict[str, list] = field(default_factory=dict)  # row filters: column -> allowed values
    group: Optional[str] = None
    capital: Optional[float] = None
    benchmark: Optional[str] = None  # an equity-level column to compare with (e.g. T-bills)
    costs_cumulative: bool = False  # cost columns are running totals (per-row change is used)

    @classmethod
    def from_config(cls, d: dict) -> "Spec":
        def lst(x):
            return [] if x is None else [x] if isinstance(x, str) else list(x)

        return cls(
            file=d["file"],
            time=lst(d.get("time") or "ts"),
            pnl=lst(d["pnl"]),
            costs=lst(d.get("costs")),
            kind=d.get("kind", "pnl"),
            costs_in_pnl=bool(d.get("costs_in_pnl", True)),
            where={k: lst(v) for k, v in (d.get("where") or {}).items()},
            group=d.get("group"),
            capital=d.get("capital"),
            benchmark=d.get("benchmark"),
            costs_cumulative=bool(d.get("costs_cumulative", False)),
        )


def standard_spec(run_dir: Path) -> Optional[Spec]:
    """The spec of a runner's standard ``performance.jsonl`` (see ``append_performance``)."""
    if not (run_dir / PERF_FILE).is_file():
        return None
    from predict_ngin.viz.paper_sources import read_table

    head = read_table(run_dir / PERF_FILE, 50, max_level=None)
    costs = [c for c in head.columns if str(c).startswith("cum_cost_")]
    return Spec(
        file=PERF_FILE,
        time=["ts"],
        pnl=["equity"],
        kind="equity",
        costs=costs,
        benchmark="benchmark",
        costs_cumulative=True,
        capital=_first_number(head, "capital"),
    )


def _first_number(df: pd.DataFrame, col: str) -> Optional[float]:
    """First finite value of ``col`` (a runner's starting capital), or None."""
    if col not in df.columns:
        return None
    x = pd.to_numeric(df[col], errors="coerce").dropna()
    return float(x.iloc[0]) if len(x) else None


def guess_costs(columns) -> List[str]:
    return [c for c in columns if any(w in str(c).lower() for w in COST_WORDS)]


def to_utc(s: pd.Series) -> pd.Series:
    """Timestamps from strings, datetimes, or numbers (ns / us / ms / s since 1970, or days)."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s, utc=True)
    if pd.api.types.is_numeric_dtype(s):
        x = pd.to_numeric(s, errors="coerce")
        m = x.abs().median()
        unit = (
            "ns"
            if m > 1e17
            else "us"
            if m > 1e14
            else "ms"
            if m > 1e11
            else "s"
            if m > 1e8
            else "D"
        )
        return pd.to_datetime(x, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(s, utc=True, errors="coerce", format="mixed")


def load(run_dir: Path, spec: Spec, max_files: int = 500) -> pd.DataFrame:
    """All rows of the spec's file(s), filtered, with a ``_t`` UTC time column."""
    from predict_ngin.viz.paper_sources import read_table

    files = sorted(glob.glob(str(run_dir / spec.file)))[-max_files:]
    parts = [read_table(Path(f), 10**9, max_level=None) for f in files]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    for col, allowed in spec.where.items():
        if col in df.columns:
            df = df[df[col].astype(str).isin([str(a) for a in allowed])]
    t = None
    for c in spec.time:
        if c in df.columns:
            tc = to_utc(df[c])
            t = tc if t is None else t.fillna(tc)
    if t is None:
        return pd.DataFrame()
    df = df.assign(_t=t).dropna(subset=["_t"]).sort_values("_t", kind="stable")
    return df.reset_index(drop=True)


def _num(df: pd.DataFrame, cols: List[str]) -> pd.Series:
    cols = [c for c in cols if c in df.columns]
    if not cols:
        return pd.Series(0.0, index=df.index)
    return df[cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).sum(axis=1)


def period_table(df: pd.DataFrame, spec: Spec, bucket: str = "D") -> pd.DataFrame:
    """Per period: ``net`` P&L, ``gross`` (before costs), one column per cost, ``cum_net``,
    ``equity`` (capital + cumulative net) and ``benchmark`` if the spec has one."""
    if df.empty:
        return pd.DataFrame()
    costs = {c: _num(df, [c]).abs() for c in spec.costs if c in df.columns}
    if spec.costs_cumulative:
        costs = {c: x.diff().fillna(x.iloc[0]) for c, x in costs.items()}
    total_cost = sum(costs.values()) if costs else pd.Series(0.0, index=df.index)
    v = _num(df, spec.pnl)
    t = df["_t"]
    rows = pd.DataFrame({"_t": t})
    if spec.kind == "pnl":
        rows["net"] = v - (0.0 if spec.costs_in_pnl else total_cost)
    else:  # a level: per-row change; the first row's change counts from capital when known
        if spec.kind == "equity":
            base = spec.capital if spec.capital is not None else v.iloc[0]
        else:  # cum_pnl
            base = 0.0
        rows["net"] = v.diff().fillna(v.iloc[0] - base)
    for c, s in costs.items():
        rows[c] = s
    rows["gross"] = rows["net"] + total_cost if (spec.kind != "pnl" or spec.costs_in_pnl) else v
    if spec.benchmark and spec.benchmark in df.columns:
        rows["benchmark"] = pd.to_numeric(df[spec.benchmark], errors="coerce")
    per = rows.set_index("_t")
    agg = {c: "sum" for c in per.columns if c != "benchmark"}
    if "benchmark" in per.columns:
        agg["benchmark"] = "last"
    out = per.resample(bucket).agg(agg)
    out["cum_net"] = out["net"].cumsum()
    cap = spec.capital
    if cap is None and spec.kind == "equity":
        cap = float(v.iloc[0])
    out["equity"] = (cap or 0.0) + out["cum_net"]
    out.index.name = "period"
    return out


def metrics(
    per: pd.DataFrame,
    capital: Optional[float],
    bucket: str = "D",
    cost_cols: Optional[List[str]] = None,
) -> Dict[str, float]:
    """Headline numbers of a period table (see the module docstring for conventions)."""
    if per.empty:
        return {}
    ppy = PERIODS.get(bucket, 365.0)
    net = per["net"].astype(float)
    n = len(net)
    cap = capital if capital else None
    out: Dict[str, float] = {
        "periods": n,
        "days": (per.index[-1] - per.index[0]).total_seconds() / 86400 + ppy**-1 * 365,
        "net_pnl": float(net.sum()),
        "gross_pnl": float(per["gross"].sum()),
        "hit_rate": float((net > 0).sum() / max((net != 0).sum(), 1)),
        "best_period": float(net.max()),
        "worst_period": float(net.min()),
        "avg_period": float(net.mean()),
    }
    wins, losses = net[net > 0], net[net < 0]
    out["profit_factor"] = float(wins.sum() / -losses.sum()) if len(losses) else math.inf
    cols = [c for c in (cost_cols or []) if c in per.columns]
    costs = {c: float(per[c].sum()) for c in cols}
    out["costs"] = float(sum(costs.values()))
    for c, v in costs.items():
        out[f"cost:{c}"] = v
    out["costs_pct_gross"] = out["costs"] / abs(out["gross_pnl"]) if out["gross_pnl"] else math.nan
    eq = per["equity"].astype(float)
    # the peak includes the starting value (capital, or 0 P&L): a loss on the first period is a
    # drawdown too
    start = float(cap) if cap else float(eq.iloc[0] - net.iloc[0])
    peak = eq.cummax().clip(lower=start)
    dd = eq - peak
    out["max_drawdown"] = float(dd.min())
    if cap:
        start_eq = eq.shift(1).fillna(cap)
        r = net / start_eq.where(start_eq > 0)
        r = r.fillna(0.0)
        out["total_return"] = float(eq.iloc[-1] / cap - 1)
        # compounding a few hours to a year overflows and means nothing: needs a week or more
        years = out["days"] / 365.0
        if out["total_return"] <= -1:
            out["ann_return"] = -1.0
        elif out["days"] < MIN_DAYS_ANNUALISE:
            out["ann_return"] = math.nan
        else:
            g = math.log1p(out["total_return"]) / years
            out["ann_return"] = math.expm1(g) if g < 700 else math.inf
        out["ann_vol"] = float(r.std(ddof=1) * math.sqrt(ppy)) if n > 1 else math.nan
        out["max_drawdown_pct"] = float((dd / peak.where(peak > 0)).min())
    else:
        r = net  # no capital: ratios on P&L itself (scale-free for Sharpe)
    sr = sharpe(r, ppy)
    out["sharpe"] = sr
    down = r[r < 0]
    dsd = math.sqrt((down**2).sum() / max(n - 1, 1)) if n > 1 else 0.0
    out["sortino"] = float(r.mean() / dsd * math.sqrt(ppy)) if dsd > 0 else math.nan
    sr_p = sr / math.sqrt(ppy)
    skew = float(r.skew()) if n > 2 else 0.0
    kurt = float(r.kurt()) + 3.0 if n > 3 else 3.0
    out["sharpe_se"] = math.sqrt((1 + 0.5 * sr_p**2) / max(n - 1, 1)) * math.sqrt(ppy)
    out["psr_gt0"] = probabilistic_sharpe(
        sr_p, n, skew if np.isfinite(skew) else 0.0, kurt if np.isfinite(kurt) else 3.0
    )
    out["t_stat"] = (
        float(net.mean() / (net.std(ddof=1) / math.sqrt(n)))
        if n > 1 and net.std(ddof=1) > 0
        else math.nan
    )
    if out.get("ann_return") is not None and out["max_drawdown_pct"] < 0:
        out["calmar"] = float(out["ann_return"] / -out["max_drawdown_pct"])
    if "benchmark" in per.columns and per["benchmark"].notna().any() and cap:
        b = per["benchmark"].astype(float).ffill()
        out["benchmark_return"] = float(b.iloc[-1] / cap - 1)
        out["excess_return"] = out["total_return"] - out["benchmark_return"]
    return out


def compare(df: pd.DataFrame, spec: Spec, bucket: str = "D", limit: int = 50) -> pd.DataFrame:
    """Headline metrics per value of ``spec.group`` (largest groups first)."""
    if df.empty or not spec.group or spec.group not in df.columns:
        return pd.DataFrame()
    rows = []
    for g in df[spec.group].astype(str).value_counts().index[:limit]:
        sub = df[df[spec.group].astype(str) == g]
        m = metrics(period_table(sub, spec, bucket), spec.capital, bucket, spec.costs)
        rows.append({spec.group: g, "rows": len(sub), **m})
    keep = [
        spec.group,
        "rows",
        "periods",
        "net_pnl",
        "gross_pnl",
        "costs",
        "total_return",
        "ann_return",
        "sharpe",
        "sharpe_se",
        "psr_gt0",
        "sortino",
        "max_drawdown",
        "hit_rate",
        "profit_factor",
    ]
    out = pd.DataFrame(rows)
    return out[[c for c in keep if c in out.columns]].sort_values("sharpe", ascending=False)
