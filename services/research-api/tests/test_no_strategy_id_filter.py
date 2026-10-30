"""Regression guard (issue #59): no query finds a book by the engine's strategy id.

One portfolio is one book. AlgoLens finds a book by its portfolio id and a sleeve by
the portfolio id and its strategy_name; the engine's strategy id (and the copy of it in
live_results.config's strategy_type) is a description of the book and is never matched
on. A filter on it hides a book whose id is relabelled and splits a book whose sleeves
changed, without an error.

This scans every string literal in the backend source (SQL included, however it is
built), so it needs no database.
"""

import ast
import os
import re

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

# A predicate on the strategy id: `strategy_id = %s`, `strategy_id IN (...)`,
# `strategy_id = ANY(...)`, `strategy_id <> ...`; or on the stored strategy_type,
# `config::jsonb->>'strategy_type'`, `strategy_type = %s`.
FORBIDDEN = (
    re.compile(r"\bstrategy_id\s*(?:=|<>|!=|\bIN\b|\bLIKE\b)", re.I),
    re.compile(r"->>\s*'strategy_type'", re.I),
    re.compile(r"\bstrategy_type\s*(?:=|<>|!=|\bIN\b|\bLIKE\b)", re.I),
)
SQL_HINT = re.compile(r"\b(?:SELECT|UPDATE|DELETE|WHERE)\b", re.I)


def _python_sources():
    for root, dirs, files in os.walk(SRC_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fname in files:
            if fname.endswith(".py"):
                yield os.path.join(root, fname)


def _sql_literals():
    """(path, line, text) for every string literal that reads like SQL."""
    for path in _python_sources():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SQL_HINT.search(node.value)
            ):
                yield path, node.lineno, node.value


def test_there_are_queries_to_check():
    """Guard the guard: a scan that finds no SQL must not silently pass."""
    found = [text for _, _, text in _sql_literals() if re.search(r"FROM trading\.positions", text)]
    assert found, "No queries on trading.positions were found; the scan is broken."


def test_no_query_filters_on_the_strategy_id_or_strategy_type():
    offenders = []
    for path, line, text in _sql_literals():
        for pattern in FORBIDDEN:
            match = pattern.search(text)
            if match:
                rel = os.path.relpath(path, SRC_DIR)
                offenders.append(f"{rel}:{line}: {match.group(0)!r}")
    assert not offenders, (
        "These queries find a book by the strategy id or strategy_type; read it by "
        "portfolio_id (and strategy_name for a sleeve) instead (issue #59):\n"
        + "\n".join(offenders)
    )
