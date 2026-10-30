"""Regression guard: equity-curve reads must name which stream they are plotting.

Companion to test_portfolio_queries.py, which guards portfolio_id. This guards
the same class of bug one dimension over.

After the dual-portfolio migration the equity curve holds three streams:

    qt         what QT actually decided -- the real book
    system     what the algorithm said today, given the real book
    benchmark  what the algorithm would have compounded to untouched

A curve query that does not constrain portfolio_type returns all three
interleaved, and the chart draws them as a single line that jumps between
portfolios. It does not error -- it just renders a picture of nothing real. That
is exactly how the portfolio_id blending (AlgoGators/AlgoLens#29) went unnoticed,
and it would arrive silently the moment the migration ran.

`_portfolio_equity_curve` drops the book predicate only on an unmigrated
database (has_portfolio_type False), which means the SQL alone cannot be
checked. This asserts every call site passes the book and the probe result.
"""

import ast
import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOSITORY = os.path.join(
    BACKEND_DIR, "src", "research_api", "infrastructure", "portfolio", "repositories.py"
)

# cursor, portfolio_id, book, has_portfolio_type
EXPECTED_ARGC = 4


def _call_sites():
    """Every call to _portfolio_equity_curve, as (lineno, arg_count) pairs.

    Parsed with ast rather than regex so nested parentheses and multi-line calls
    cannot fool it.
    """
    with open(REPOSITORY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "_portfolio_equity_curve":
            sites.append((node.lineno, len(node.args) + len(node.keywords)))
    return sites


def test_there_are_call_sites_to_check():
    """Guard the guard: a scan that finds nothing must not silently pass."""
    assert _call_sites(), (
        "No calls to _portfolio_equity_curve were found. Either it was renamed or the "
        "scan is broken -- do not treat this as a pass."
    )


def test_every_equity_curve_read_names_its_stream():
    unscoped = [lineno for lineno, argc in _call_sites() if argc < EXPECTED_ARGC]

    assert not unscoped, (
        "_portfolio_equity_curve is called without a book at repositories.py line(s) "
        + ", ".join(str(n) for n in unscoped)
        + ". Those reads will blend qt, system and benchmark into a single line."
    )
