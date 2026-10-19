"""Data model of the portfolio report: sources -> books -> strategies -> a tree of nodes.

A **source** is a function returning a list of :class:`Book`. A book is a capital pool (one
paper or forward run). ``Book.daily`` has one row per UTC day and strategy with columns
``date, strategy, net, gross`` plus one column per cost type (positive dollars). ``net`` is
after costs. Register a source with::

    from predict_ngin.viz.report import Book, register_source

    @register_source("my_backtests")
    def my_backtests():
        daily = ...  # DataFrame: date, strategy, net, gross, fees, slippage
        return [Book("my book", daily, capital=10_000, cost_cols=["fees", "slippage"])]

:func:`build_tree` turns the books into nodes (portfolio, book, strategy). Each node gets the
same metrics (:func:`predict_ngin.viz.performance.metrics`, daily periods) and series. Returns
use the node's capital: the book's capital for a book, the sum for the portfolio, and for a
strategy its ``strategy_capital`` if given (otherwise only dollar figures and P&L-based ratios).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from predict_ngin.viz import performance as perf

_SOURCES: Dict[str, Callable[[], List["Book"]]] = {}


def register_source(name: str):
    def deco(fn):
        _SOURCES[name] = fn
        return fn

    return deco


def sources() -> Dict[str, Callable[[], List["Book"]]]:
    from . import sources_builtin  # noqa: F401  (registers the built-in sources)

    return dict(_SOURCES)


@dataclass
class Book:
    name: str
    daily: pd.DataFrame  # date, strategy, net, gross, <cost columns>
    capital: Optional[float] = None
    cost_cols: List[str] = field(default_factory=list)
    benchmark: Optional[pd.Series] = None  # equity level of a cash benchmark, by date
    strategy_capital: Dict[str, float] = field(default_factory=dict)
    source: str = ""
    note: str = ""


@dataclass
class Node:
    id: str
    name: str
    level: str  # portfolio | book | strategy
    parent: Optional[str]
    per: pd.DataFrame  # daily: net, gross, costs..., cum_net, equity (+ benchmark)
    capital: Optional[float]
    cost_cols: List[str]
    children: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    note: str = ""


def _period_table(
    daily: pd.DataFrame,
    cost_cols: List[str],
    capital: Optional[float],
    benchmark: Optional[pd.Series],
    index: pd.DatetimeIndex,
) -> pd.DataFrame:
    cols = ["net", "gross", *cost_cols]
    d = daily.groupby("date")[[c for c in cols if c in daily.columns]].sum()
    d.index = pd.to_datetime(d.index, utc=True)
    d = d.reindex(index, fill_value=0.0)
    for c in cols:
        if c not in d.columns:
            d[c] = 0.0
    d["cum_net"] = d["net"].cumsum()
    d["equity"] = (capital or 0.0) + d["cum_net"]
    if benchmark is not None and len(benchmark):
        b = benchmark.copy()
        b.index = pd.to_datetime(b.index, utc=True)
        d["benchmark"] = b.reindex(index).ffill()
    d.index.name = "period"
    return d


def build_tree(books: List[Book]) -> Dict[str, Node]:
    """Nodes keyed by id: ``portfolio``, ``book:<name>``, ``strategy:<book>/<name>``."""
    books = [b for b in books if b.daily is not None and not b.daily.empty]
    nodes: Dict[str, Node] = {}
    if not books:
        return nodes
    start = min(pd.to_datetime(b.daily["date"], utc=True).min() for b in books)
    end = max(pd.to_datetime(b.daily["date"], utc=True).max() for b in books)
    index = pd.date_range(start.normalize(), end.normalize(), freq="D", tz="UTC")
    all_costs = sorted({c for b in books for c in b.cost_cols})
    port_daily = []
    for b in books:
        bid = f"book:{b.name}"
        bdays = pd.to_datetime(b.daily["date"], utc=True)
        bidx = index[(index >= bdays.min().normalize()) & (index <= bdays.max().normalize())]
        per = _period_table(b.daily, b.cost_cols, b.capital, b.benchmark, bidx)
        nodes[bid] = Node(
            bid, b.name, "book", "portfolio", per, b.capital, b.cost_cols, note=b.note
        )
        for s in sorted(b.daily["strategy"].astype(str).unique()):
            sid = f"strategy:{b.name}/{s}"
            cap = b.strategy_capital.get(s)
            sper = _period_table(
                b.daily[b.daily["strategy"].astype(str) == s], b.cost_cols, cap, None, bidx
            )
            nodes[sid] = Node(sid, s, "strategy", bid, sper, cap, b.cost_cols)
            nodes[bid].children.append(sid)
        port_daily.append(b.daily.assign(strategy=b.name))
    caps = [b.capital for b in books]
    known = [c for c in caps if c is not None]
    pcap = float(sum(known)) if len(known) == len(caps) else None
    pd_all = pd.concat(port_daily, ignore_index=True)
    pper = _period_table(pd_all, all_costs, pcap, None, index)
    nodes["portfolio"] = Node(
        "portfolio",
        "Portfolio",
        "portfolio",
        None,
        pper,
        pcap,
        all_costs,
        children=[f"book:{b.name}" for b in books],
    )
    for n in nodes.values():
        n.metrics = perf.metrics(n.per, n.capital, "D", n.cost_cols)
        if len(n.children) > 1:
            n.metrics["diversification"] = _diversification(nodes, n)
    return nodes


def _diversification(nodes: Dict[str, Node], n: Node) -> float:
    """Sum of the children's P&L volatility over the node's P&L volatility (1 = no benefit)."""
    vols = [nodes[c].per["net"].std(ddof=1) for c in n.children]
    tot = n.per["net"].std(ddof=1)
    if not tot or not np.isfinite(tot) or tot == 0:
        return math.nan
    return float(np.nansum(vols) / tot)


def correlations(nodes: Dict[str, Node], node_id: str) -> pd.DataFrame:
    """Correlation of daily net P&L between a node's children (days where either is active)."""
    kids = nodes[node_id].children
    if len(kids) < 2:
        return pd.DataFrame()
    df = pd.DataFrame({nodes[k].name: nodes[k].per["net"] for k in kids})
    df = df.loc[(df != 0).any(axis=1)]
    return df.corr() if len(df) >= 3 else pd.DataFrame()


def monthly(per: pd.DataFrame, capital: Optional[float]) -> pd.DataFrame:
    """Net P&L (and return on capital at the start of the month) by calendar month."""
    m = per["net"].groupby(per.index.strftime("%Y-%m")).sum().rename("net").to_frame()
    if capital:
        start = per["equity"].shift(1).fillna(capital).groupby(per.index.strftime("%Y-%m")).first()
        m["return"] = m["net"] / start.where(start > 0)
    return m
