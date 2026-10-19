"""Portfolio report: portfolio -> books -> strategies, metrics and charts at every level.

    uv run --no-sync predict-ngin report   # writes data/reports/portfolio.html, opens it
    uv run --no-sync python -m predict_ngin.viz.report --no-open --out some.html

One self-contained HTML file (data and the chart library inlined; works offline). Charts use
TradingView Lightweight Charts (Apache 2.0, vendored in ``vendor/``).

Adding data is one function: register a *source* that returns :class:`Book` objects, each with a
daily P&L table per strategy (see :mod:`.model`). The built-in source reads the paper / forward
runs that have a ``performance:`` spec in ``config/paper_monitor.local.yaml`` (or a standard
``performance.jsonl``). Everything else (metrics, aggregation, charts) works on any source.
"""

from .model import Book, Node, build_tree, register_source, sources

__all__ = ["Book", "Node", "build_tree", "register_source", "sources"]
