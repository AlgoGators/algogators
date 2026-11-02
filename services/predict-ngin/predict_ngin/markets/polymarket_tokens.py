"""Polymarket YES/NO token identity: parsing ``clobTokenIds`` / ``outcomes`` and the YES convention.

Convention (docs/PMFIX_POLYMARKET_TOKEN_NORMALISATION.md section 2): the
"YES-equivalent" token of a market is outcome index 0, ``clobTokenIds[0]``. For Yes/No markets
that is "Yes"; for named-outcome markets it is the first listed outcome.

This module is the future home of ``src/data_quality/polymarket_tokens.py`` (``TokenMap``,
``normalize_trades``, ...). That move is deferred while the module has uncommitted edits;
until then :func:`parse_list_field` here is a verbatim copy of the one there, and a golden test
keeps the two identical.
"""

from __future__ import annotations

from typing import Mapping, Optional

import numpy as np

__all__ = ["YES_INDEX", "parse_list_field", "clob_token_ids", "outcome_labels", "yes_no_token_ids"]

YES_INDEX = 0  # clobTokenIds[0]


def parse_list_field(value) -> list[str]:
    """Parse ``clobTokenIds`` / ``outcomes`` strings in any of the on-disk encodings.

    Handles JSON (``'["a", "b"]'``), Python reprs (``"['a', 'b']"``), the doubled-quote CSV export
    used in ``data/pmxt/markets`` (``'[""""a"""", """"b""""]'``), real lists/arrays and missing
    values.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, np.ndarray)):
        return [str(v).strip() for v in value]
    if isinstance(value, float) and np.isnan(value):
        return []
    s = str(value).strip()
    if not s:
        return []
    s = s.replace('"', "").replace("'", "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [x.strip() for x in s.split(",") if x.strip()]


def clob_token_ids(market: Mapping) -> list[str]:
    """Token ids of a Gamma market record, in outcome order."""
    return parse_list_field(market.get("clobTokenIds"))


def outcome_labels(market: Mapping) -> list[str]:
    """Outcome labels of a Gamma market record, in the same order as its token ids."""
    return parse_list_field(market.get("outcomes"))


def yes_no_token_ids(market: Mapping) -> Optional[tuple[str, str]]:
    """(YES-equivalent token, other token) of a binary market, or None if it does not have two."""
    toks = clob_token_ids(market)
    if len(toks) != 2:
        return None
    return toks[YES_INDEX], toks[1 - YES_INDEX]
