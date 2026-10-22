"""Write the portfolio report: one self-contained HTML file (data + chart library inlined).

uv run --no-sync python -m predict_ngin.viz.report [--out PATH] [--sources a,b] [--no-open]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from predict_ngin.shared.paths import data_root

from .model import Book, Node, build_tree, correlations, monthly, sources

HERE = Path(__file__).parent
LWC = HERE / "vendor" / "lightweight-charts-4.2.3.standalone.production.js"
ROLL_MIN_DAYS = 40
ROLL_WINDOW = 30


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        x = float(x)
        if math.isnan(x):
            return None
        if math.isinf(x):
            return "inf" if x > 0 else "-inf"
        return round(x, 6)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def _series(n: Node) -> dict:
    """Chart series. Level series start one day before the first period at the starting value
    (capital, or 0 P&L), so a loss on day one shows in equity and drawdown."""
    p = n.per
    costs = (
        p[[c for c in n.cost_cols if c in p.columns]].sum(axis=1) if n.cost_cols else 0.0 * p["net"]
    )
    start = (p.index[0] - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    base = float(n.capital or 0.0)
    eq = p["equity"] if n.capital else p["cum_net"]
    peak = eq.cummax().clip(lower=base)
    s = {
        "dates": [start] + [t.strftime("%Y-%m-%d") for t in p.index],
        "equity": [base] + eq.tolist(),
        "net": [None] + p["net"].tolist(),
        "cum_net": [0.0] + p["cum_net"].tolist(),
        "cum_gross": [0.0] + p["gross"].cumsum().tolist(),
        "cum_costs": [0.0] + (-costs.cumsum()).tolist(),
        "drawdown": [0.0] + (eq - peak).tolist(),
    }
    if "benchmark" in p.columns and p["benchmark"].notna().any():
        s["benchmark"] = [base if n.capital else None] + p["benchmark"].tolist()
    if len(p) >= ROLL_MIN_DAYS:
        prev = p["equity"].shift(1).fillna(n.capital or 0.0)
        r = p["net"] / prev.where(prev > 0) if n.capital else p["net"]
        roll = r.rolling(ROLL_WINDOW).mean() / r.rolling(ROLL_WINDOW).std() * math.sqrt(365)
        s["rolling_sharpe"] = [None] + roll.tolist()
        s["roll_window"] = ROLL_WINDOW
    return s


def payload(nodes: Dict[str, Node], source_names: List[str]) -> dict:
    out = {}
    for nid, n in nodes.items():
        c = correlations(nodes, nid)
        mo = (
            monthly(n.per, n.capital)
            .reset_index()
            .rename(columns={"index": "month", "period": "month"})
        )
        out[nid] = {
            "name": n.name,
            "level": n.level,
            "parent": n.parent,
            "children": n.children,
            "capital": n.capital,
            "note": n.note,
            "metrics": n.metrics,
            "series": _series(n),
            "monthly": mo.to_dict("records"),
            "corr": {"names": list(c.columns), "values": c.values.tolist()}
            if not c.empty
            else None,
        }
    return _clean(
        {
            "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "sources": source_names,
            "nodes": out,
        }
    )


def render_html(
    nodes: Dict[str, Node], source_names: List[str], title: str = "predict-ngin portfolio"
) -> str:
    data = json.dumps(payload(nodes, source_names), separators=(",", ":"), allow_nan=False)
    data = data.replace("</", "<\\/")  # never close the script tag from inside the data
    html = (HERE / "template.html").read_text(encoding="utf-8")
    return (
        html.replace("__TITLE__", title)
        .replace("__LWC__", LWC.read_text(encoding="utf-8"))
        .replace("__DATA__", data)
    )


def collect(names: Optional[List[str]] = None) -> tuple[List[Book], List[str]]:
    reg = sources()
    use = names or list(reg)
    books: List[Book] = []
    for name in use:
        if name not in reg:
            raise SystemExit(f"unknown source {name!r}; known: {', '.join(reg)}")
        books += reg[name]()
    return books, use


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="predict-ngin report", description="portfolio / book / strategy report (HTML)"
    )
    ap.add_argument("--out", default=None, help="default: <data>/reports/portfolio.html")
    ap.add_argument("--sources", help="comma list of registered sources (default: all)")
    ap.add_argument("--no-open", action="store_true", help="do not open it in the browser")
    a = ap.parse_args(argv)
    books, used = collect(a.sources.split(",") if a.sources else None)
    nodes = build_tree(books)
    if not nodes:
        print(
            "no books with P&L found; add `performance:` specs in "
            "config/paper_monitor.local.yaml or register a source (see predict_ngin.viz.report)",
            file=sys.stderr,
        )
        return 1
    out = Path(a.out) if a.out else data_root() / "reports" / "portfolio.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(nodes, used), encoding="utf-8")
    m = nodes["portfolio"].metrics
    print(
        f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB): {len(books)} book(s), "
        f"{sum(1 for n in nodes.values() if n.level == 'strategy')} strategies, "
        f"net {m.get('net_pnl', float('nan')):,.2f}"
    )
    if not a.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0
