"""Golden tests for step 5.3 (Polymarket read client + token parsing).

* ``predict_ngin.markets.polymarket_tokens.parse_list_field`` equals the one in
  ``src/data_quality/polymarket_tokens.py`` (the module that will move there later).
* The three replaced clobTokenIds parsers give the same lists as before on every input the old
  code could parse (JSON, Python reprs, lists, missing values). On the doubled-quote export used by
  the files on disk, the old parsers returned [] (a bug); the canonical parser returns the ids.
* ``PolymarketReadClient`` routes each call to the right host, paginates, and is GET only.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "data"))

import _legacy_polymarket as LG  # noqa: E402

from predict_ngin.markets import polymarket_tokens as PT  # noqa: E402
from predict_ngin.venues.http import ReadOnlyViolation  # noqa: E402
from predict_ngin.venues.polymarket import (  # noqa: E402
    CLOB_URL,
    DATA_API_URL,
    GAMMA_URL,
    PolymarketReadClient,
)

T1 = "68403617023928319391393106546921684310232046613960318014925012281012617633113"
T2 = "26438958968580962785252726211794859109712312880011398233001813093405611227468"
PARSEABLE = [
    json.dumps([T1, T2]), json.dumps([T1, T2], separators=(",", ":")), json.dumps([T1]), json.dumps([]),
    repr([T1, T2]), f"  {json.dumps([T1, T2])}  ", f'["{T1}", "{T2}", "123"]',
    None, float("nan"), "", "[]",
]
DOUBLED = [f'[""""{T1}"""", """"{T2}""""]', f'[""""{T1}""""]']


def _parsers():
    from src.trading.data_modules.fetcher import DataFetcher
    import collect_research_by_market_list as C
    import fetch_research_trades_and_prices as F
    return [("fetcher", lambda v: DataFetcher._parse_token_list(None, v), LG.fetcher_parse_token_list),
            ("collect", C._parse_clob_token_ids, LG.collect_parse_clob_token_ids),
            ("fetch_research", F._parse_clob_token_ids, LG.fetch_parse_clob_token_ids)]


def test_canonical_parser_is_the_data_quality_one():
    from src.data_quality.polymarket_tokens import YES_INDEX, parse_list_field
    vals = PARSEABLE + DOUBLED + [[T1, T2], (T1,), np.array([T1, T2]), "Yes, No", '["Yes", "No"]',
                                  "['Up', 'Down']", "[1, 2]", "  ", '[""""Yes"""", """"No""""]']
    for v in vals:
        assert PT.parse_list_field(v) == parse_list_field(v), v
    assert PT.YES_INDEX == YES_INDEX == 0


def test_replaced_parsers_match_legacy_on_parseable_inputs():
    reprs = [v for v in PARSEABLE if isinstance(v, str) and v.startswith("['")]
    for name, new, old in _parsers():
        for v in PARSEABLE:
            if name == "fetcher" and (v in reprs or (v is not None and not isinstance(v, str))):
                continue  # JSON-only parser; it never sees NaN floats
            assert new(v) == old(v), (name, v)
        if name == "fetcher":
            assert new([T1, T2]) == old([T1, T2]) == [T1, T2]
            for v in reprs:                                  # old: JSON-only, returned [] for reprs
                assert old(v) == [] and new(v) == [T1, T2]


def test_replaced_parsers_fix_the_doubled_quote_export():
    for name, new, old in _parsers():
        for v in DOUBLED:
            assert old(v) == []                              # the old bug: no tokens at all
            assert new(v) == PT.parse_list_field(v) and new(v)[0] == T1


@pytest.mark.data
def test_parsers_on_files_on_disk():
    vals = []
    for f in glob.glob(str(ROOT / "data" / "research" / "*" / "markets_filtered.csv")):
        vals += pd.read_csv(f, usecols=["clobTokenIds"]).clobTokenIds.tolist()
    pmxt = ROOT / "data" / "pmxt" / "markets" / "markets_all.parquet"
    if pmxt.exists():
        vals += pd.read_parquet(pmxt, columns=["clobTokenIds"]).clobTokenIds.tolist()
    if not vals:
        pytest.skip("no Polymarket market metadata on disk")
    for name, new, old in _parsers():
        for v in vals:
            toks = new(v)
            assert toks and all(t.isdigit() for t in toks), (name, str(v)[:40])
            assert old(v) in ([], toks)


def test_market_record_helpers():
    m = {"clobTokenIds": json.dumps([T1, T2]), "outcomes": '["Yes", "No"]'}
    assert PT.clob_token_ids(m) == [T1, T2]
    assert PT.outcome_labels(m) == ["Yes", "No"]
    assert PT.yes_no_token_ids(m) == (T1, T2)
    assert PT.yes_no_token_ids({"clobTokenIds": json.dumps([T1])}) is None
    assert PolymarketReadClient.yes_no_tokens(m) == (T1, T2)


# ------------------------------------------------------------------------------------ client
class Router:
    """Fake session: answers by (host, path); records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        key = url
        if key not in self.answers:
            status, body = 404, {"error": "not found"}
        else:
            a = self.answers[key]
            body = a(params or {}) if callable(a) else a
            status = 200
        r = requests.Response()
        r.status_code, r._content, r.url, r.reason = status, json.dumps(body).encode(), url, "x"
        return r


def _client(answers):
    sess = Router(answers)
    return PolymarketReadClient(session=sess, rates={"gamma": 1000, "clob": 1000, "data": 1000},
                                sleep=lambda s: None), sess


def test_client_routes_and_paginates():
    markets = [{"id": i} for i in range(250)]
    trades = [{"i": i} for i in range(1200)]
    answers = {
        GAMMA_URL + "/markets": lambda q: markets[q["offset"]:q["offset"] + q["limit"]],
        GAMMA_URL + "/events": [{"id": "e1"}],
        GAMMA_URL + "/markets/42": {"id": 42},
        CLOB_URL + "/book": lambda q: {"asset_id": q["token_id"], "bids": [], "asks": []},
        CLOB_URL + "/prices-history": lambda q: {"history": [{"t": 1, "p": 0.4}], "q": q},
        CLOB_URL + "/markets": lambda q: ({"data": [1, 2], "next_cursor": "MTA="} if not q
                                          else {"data": [3], "next_cursor": "LTE="}),
        DATA_API_URL + "/trades": lambda q: trades[q.get("offset", 0):q.get("offset", 0) + q.get("limit", 100)],
    }
    c, sess = _client(answers)
    assert [m["id"] for m in c.iter_gamma("/markets", {"closed": "true"})] == list(range(250))
    assert sess.calls[0] == (GAMMA_URL + "/markets", {"closed": "true", "limit": 100, "offset": 0})
    assert c.gamma_events(tag="x") == [{"id": "e1"}]
    assert c.gamma_market(42) == {"id": 42}
    assert c.gamma_market(43) is None                                     # 404 -> None
    assert c.clob_book(T1)["asset_id"] == T1
    assert c.clob_prices_history(T1, interval="max") == [{"t": 1, "p": 0.4}]
    assert sess.calls[-1] == (CLOB_URL + "/prices-history", {"market": T1, "interval": "max"})
    assert list(c.iter_clob_markets()) == [1, 2, 3]
    assert len(list(c.iter_data_trades({"market": "0xabc"}, page=500))) == 1200
    assert c.data_trades(market="0xabc", limit=5)[-1] == {"i": 4}
    assert sess.headers["User-Agent"].startswith("predict-ngin")


def test_client_is_get_only_and_hosts_are_separate():
    c, _ = _client({})
    for host in (c.gamma, c.clob, c.data):
        with pytest.raises(ReadOnlyViolation):
            host.request("POST", "/order")
        assert not hasattr(host, "post")
    assert {c.gamma.base_url, c.clob.base_url, c.data.base_url} == {GAMMA_URL, CLOB_URL, DATA_API_URL}
    assert len({id(c.gamma.limiter), id(c.clob.limiter), id(c.data.limiter)}) == 3


def test_fetch_research_uses_shared_urls():
    import fetch_research_trades_and_prices as F
    from src.trading.data_modules import fetcher
    assert (F.DATA_API, F.CLOB_API, fetcher.POLYMARKET_CLOB_API) == (DATA_API_URL, CLOB_URL, CLOB_URL)
