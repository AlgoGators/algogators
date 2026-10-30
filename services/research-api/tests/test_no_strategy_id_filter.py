"""Regression guard (issue #59): no query finds a book by the engine's strategy id.

One portfolio is one book. AlgoLens finds a book by its portfolio id and a sleeve by
the portfolio id and its strategy_name; the engine's strategy id (and the copy of it in
live_results.config's strategy_type) is a description of the book and is never matched
on. A filter on it hides a book whose id is relabelled and splits a book whose sleeves
changed, without an error.

This scans the backend source rather than executing SQL, so it needs no database. It
reads every string that is SQL or a piece of one: a literal or f-string with a SQL
keyword, and a filter fragment built on its own (`"AND ... = %s"`, spliced into a query
as `{book_filter}` is). An f-string is read whole, its placeholders as `{}`, so a filter
after a placeholder is still seen.
"""

import ast
import os
import re

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

_ID = r"""(?:\b|")strategy_id"?"""
_TYPE = r"""(?:\b|")strategy_type"?"""
_COMPARE = r"\s*(?:=|<>|!=|\bIN\b|\bLIKE\b|\bIS\b)"
# A predicate on the strategy id or on the stored strategy_type, however it is spelled.
FORBIDDEN = (
    re.compile(_ID + _COMPARE, re.I),
    re.compile(_TYPE + _COMPARE, re.I),
    re.compile(r"->>?\s*'strategy_type'", re.I),
    re.compile(r"""@>\s*'\{\s*"strategy_type\"""", re.I),
)
# Not a filter: an upsert's `col = EXCLUDED.col`, and an assignment in an UPDATE's SET list
# (the nearest clause keyword before it is SET).
EXCLUDED_ASSIGNMENT = re.compile(r"strategy_(?:id|type)\s*=\s*EXCLUDED\.", re.I)
CLAUSE = re.compile(r"\b(SET|WHERE|AND|OR|ON|HAVING|WHEN)\b", re.I)
SQL_HINT = re.compile(r"\b(?:SELECT|UPDATE|DELETE|WHERE)\b", re.I)
FRAGMENT = re.compile(r"^\s*(?:AND|OR)\b", re.I)


def _python_sources():
    for root, dirs, files in os.walk(SRC_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fname in files:
            if fname.endswith(".py"):
                yield os.path.join(root, fname)


def _text(node):
    """A string literal's text, or an f-string's with each placeholder as `{}`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "{}" for part in node.values
        )
    return None


def _sql_strings():
    """(path, line, text) for every string that is SQL or a filter fragment of SQL."""
    for path in _python_sources():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            text = _text(node)
            if text and (SQL_HINT.search(text) or FRAGMENT.search(text)):
                yield path, node.lineno, text


def _offences(text):
    found = []
    for pattern in FORBIDDEN:
        for match in pattern.finditer(text):
            if EXCLUDED_ASSIGNMENT.match(text, match.start()):
                continue
            clauses = CLAUSE.findall(text, 0, match.start())
            if clauses and clauses[-1].upper() == "SET":
                continue
            found.append(match.group(0))
    return found


def test_there_are_queries_to_check():
    """Guard the guard: a scan that finds no SQL must not silently pass."""
    found = [text for _, _, text in _sql_strings() if "FROM trading.positions" in text]
    assert found, "No queries on trading.positions were found; the scan is broken."


def test_no_query_filters_on_the_strategy_id_or_strategy_type():
    offenders = []
    for path, line, text in _sql_strings():
        for found in _offences(text):
            offenders.append(f"{os.path.relpath(path, SRC_DIR)}:{line}: {found!r}")
    assert not offenders, (
        "These queries find a book by the strategy id or strategy_type; read it by "
        "portfolio_id (and strategy_name for a sleeve) instead (issue #59):\n"
        + "\n".join(offenders)
    )


def test_the_scan_sees_the_ways_a_filter_is_written():
    caught = [
        "SELECT * FROM trading.positions WHERE strategy_id = %s",
        "AND strategy_id = %s",  # a fragment spliced in like {book_filter}
        "SELECT 1 FROM t WHERE portfolio_id = %s {} AND strategy_id = %s",  # after a placeholder
        'SELECT 1 FROM t WHERE "strategy_id" = %s',
        "SELECT 1 FROM trading.live_results WHERE config::jsonb->>'strategy_type' = %s",
        "SELECT 1 FROM trading.live_results WHERE config -> 'strategy_type' = %s",
        """SELECT 1 FROM trading.live_results WHERE config @> '{"strategy_type": "X"}'""",
        "SELECT 1 FROM t WHERE strategy_type IN (%s)",
    ]
    for sql in caught:
        assert _offences(sql), sql
    passed = [
        (
            "INSERT INTO t (strategy_id, x) VALUES (%s, %s) "
            + "ON CONFLICT (k) DO UPDATE SET strategy_id = EXCLUDED.strategy_id"
        ),
        "UPDATE trading.positions SET quantity = %s, strategy_id = %s WHERE portfolio_id = %s",
        "SELECT symbol, strategy_id, strategy_name FROM trading.positions WHERE portfolio_id = %s",
    ]
    passed.append("UPDATE t SET strategy_id = %s WHERE portfolio_id = %s")
    caught_after_set = "UPDATE t SET quantity = %s WHERE strategy_id = %s"
    assert _offences(caught_after_set), caught_after_set
    for sql in passed:
        assert not _offences(sql), sql
