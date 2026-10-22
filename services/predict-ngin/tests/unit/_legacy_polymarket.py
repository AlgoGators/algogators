"""Frozen copies of the clobTokenIds parsers replaced in step 5.3 (2026-10-15), verbatim as of
commit bc121ac. Do not edit; the golden tests compare against these."""
from __future__ import annotations

import ast
import json
from typing import Any, List

import pandas as pd


def fetcher_parse_token_list(tokens_val: Any) -> List[str]:   # src/trading/data_modules/fetcher.py:396
    if isinstance(tokens_val, str):
        try:
            tokens_val = json.loads(tokens_val)
        except json.JSONDecodeError:
            return []
    if isinstance(tokens_val, (list, tuple)):
        return [str(t) for t in tokens_val if t is not None]
    return []


def collect_parse_clob_token_ids(raw) -> List[str]:           # scripts/data/collect_research_by_market_list.py:50
    if pd.isna(raw) or raw == "" or raw == "[]":
        return []
    s = str(raw).strip()
    if s.startswith("["):
        try:
            out = ast.literal_eval(s)
            return [str(x) for x in out] if isinstance(out, list) else []
        except Exception:
            pass
    return []


def fetch_parse_clob_token_ids(raw: Any) -> List[str]:        # scripts/data/fetch_research_trades_and_prices.py:37
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    s = str(raw).strip()
    if not s or s == "[]":
        return []
    if s.startswith("["):
        try:
            out = ast.literal_eval(s)
            return [str(x).strip() for x in out] if isinstance(out, list) else []
        except Exception:
            pass
    return []
