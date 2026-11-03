"""Black-swan stress test of a held book, on the command line (`stress-test`).

Tier 1: replay each catalogued event's per-instrument moves, rescaled to today's volatility,
against a book held unchanged, for the system, proposal and final books on a day and for a
book with proposed edits.

Tier 2: the same moves as daily bars, traded through by the trade-ngin engine as published (its
public Python module, unchanged): the strategy's own book, plus the tested book's difference from
it carried alongside.

Both read a local snapshot; only `snapshot` connects to a database, read-only.
"""
