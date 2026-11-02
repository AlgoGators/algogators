"""Portfolio report: tree aggregation, metrics at each level, and the self-contained HTML."""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from predict_ngin.viz.report.model import Book, build_tree, correlations, monthly
from predict_ngin.viz.report.render import payload, render_html


def _books():
    days = pd.date_range("2026-01-01", periods=45, freq="D", tz="UTC")
    rng = np.random.default_rng(0)

    def strat(name, mu, start=0):
        g = rng.normal(mu, 10, len(days) - start)
        return pd.DataFrame(
            {"date": days[start:], "strategy": name, "gross": g + 1, "fees": 1.0, "net": g}
        )

    a = Book(
        "A",
        pd.concat([strat("x", 2), strat("y", -1, start=5)]),
        capital=1000,
        cost_cols=["fees"],
        strategy_capital={"x": 500},
    )
    b = Book("B <b>", strat("z", 1), capital=2000, cost_cols=["fees"])
    return a, b


def test_tree_aggregates_and_metrics():
    a, b = _books()
    nodes = build_tree([a, b])
    assert set(nodes) == {
        "portfolio",
        "book:A",
        "book:B <b>",
        "strategy:A/x",
        "strategy:A/y",
        "strategy:B <b>/z",
    }
    p, ba = nodes["portfolio"], nodes["book:A"]
    assert p.capital == 3000 and p.children == ["book:A", "book:B <b>"]
    total = a.daily["net"].sum() + b.daily["net"].sum()
    assert p.metrics["net_pnl"] == pytest.approx(total)
    assert ba.metrics["net_pnl"] == pytest.approx(
        nodes["strategy:A/x"].metrics["net_pnl"] + nodes["strategy:A/y"].metrics["net_pnl"]
    )
    assert ba.metrics["costs"] == pytest.approx(45 + 40)  # y starts 5 days later
    assert nodes["strategy:A/x"].capital == 500 and nodes["strategy:A/y"].capital is None
    assert "total_return" in nodes["strategy:A/x"].metrics
    assert np.isfinite(p.metrics["diversification"])
    assert correlations(nodes, "book:A").shape == (2, 2)
    assert monthly(p.per, p.capital)["net"].sum() == pytest.approx(total)


def test_drawdown_counts_a_first_day_loss():
    d = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=2, tz="UTC"),
            "strategy": "s",
            "net": [-100.0, 0.0],
            "gross": [-100.0, 0.0],
        }
    )
    n = build_tree([Book("A", d, capital=1000)])["portfolio"]
    assert n.metrics["max_drawdown"] == pytest.approx(-100)
    s = payload({"portfolio": n}, ["t"])["nodes"]["portfolio"]["series"]
    assert s["equity"][0] == 1000 and s["drawdown"][-1] == pytest.approx(-100)


def test_html_is_self_contained_and_safe():
    a, b = _books()
    b.daily = b.daily.assign(strategy="</script><script>alert(1)</script>")
    html = render_html(build_tree([a, b]), ["test"])
    assert "LightweightCharts" in html and "__DATA__" not in html
    data = re.search(r"const DATA = (.*?);\nconst N", html, re.S).group(1)
    assert "</script" not in data  # names cannot close the script tag
    doc = json.loads(data.replace("<\/", "</"))
    assert doc["nodes"]["portfolio"]["series"]["dates"][0] == "2025-12-31"
    assert "http" not in re.sub(
        r"https://www\.(tradingview|apache)\.\w+[^\s\"'<]*",
        "",
        html.split("const DATA")[0].split("<script>", 1)[0],
    )


def test_cli_writes_report(tmp_path, monkeypatch):
    from predict_ngin.viz.report import model, render

    a, b = _books()
    monkeypatch.setattr(render, "sources", lambda: {"t": lambda: [a, b]})
    out = tmp_path / "r.html"
    assert render.main(["--out", str(out), "--no-open"]) == 0
    assert out.stat().st_size > 150_000
    monkeypatch.setattr(render, "sources", lambda: {"t": lambda: []})
    assert render.main(["--out", str(out), "--no-open"]) == 1
    assert model.build_tree([]) == {}
