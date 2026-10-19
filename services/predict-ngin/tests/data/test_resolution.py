import sqlite3
from pathlib import Path
from unittest.mock import patch

import requests

from trading.data_modules.resolution import (
    add_resolution_columns,
    determine_winner,
    fetch_resolved_markets,
    load_resolution_cache,
    parse_resolution_data,
    save_resolution_cache,
    update_resolution_data,
)


def test_determine_winner_thresholds() -> None:
    assert determine_winner(["0.96", "0.04"]) == "YES"
    assert determine_winner(["0.04", "0.97"]) == "NO"
    assert determine_winner(["0", "0"]) is None
    assert determine_winner(["bad", "0.0"]) is None
    assert determine_winner([]) is None


def test_parse_resolution_data_parses_strings() -> None:
    market = {
        "id": "m1",
        "slug": "m1",
        "question": "Q" * 250,
        "outcomePrices": '["0.99", "0.01"]',
        "closed": True,
        "closedTime": "2026-01-01",
        "endDate": "2026-01-02",
        "volume": "1234.5",
    }

    parsed = parse_resolution_data(market)

    assert parsed["winner"] == "YES"
    assert parsed["resolution_price_yes"] == 0.99
    assert parsed["resolution_price_no"] == 0.01
    assert parsed["volume"] == 1234.5
    assert len(parsed["question"]) == 200


def _create_resolution_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE polymarket_markets (id TEXT PRIMARY KEY, slug TEXT, question TEXT, volume REAL)"
    )
    cursor.execute(
        "INSERT INTO polymarket_markets (id, slug, question, volume) VALUES (?, ?, ?, ?)",
        ("m1", "m1", "Question", 100.0),
    )
    conn.commit()
    conn.close()


def test_add_resolution_columns_and_update(tmp_path: Path) -> None:
    db_path = tmp_path / "resolution.db"
    _create_resolution_db(db_path)

    assert add_resolution_columns(str(db_path)) is True

    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(polymarket_markets)").fetchall()}
    conn.close()

    assert "resolution_outcome" in cols
    assert "resolution_price_yes" in cols
    assert "resolution_price_no" in cols

    updated, skipped = update_resolution_data(
        str(db_path),
        [
            {
                "market_id": "m1",
                "closed": True,
                "closed_time": "2026-01-01",
                "winner": "YES",
                "resolution_price_yes": 0.99,
                "resolution_price_no": 0.01,
            },
            {
                "market_id": "m2",
                "closed": True,
                "closed_time": "2026-01-01",
                "winner": "NO",
                "resolution_price_yes": 0.0,
                "resolution_price_no": 1.0,
            },
        ],
    )

    assert updated == 1
    assert skipped == 1

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT closed, resolution_outcome, resolution_price_yes, resolution_price_no "
        "FROM polymarket_markets WHERE id = ?",
        ("m1",),
    ).fetchone()
    conn.close()

    assert row == (1, "YES", 0.99, 0.01)


def test_resolution_cache_round_trip(tmp_path: Path) -> None:
    markets = [{"market_id": "m1", "winner": "YES"}]
    cache_path = tmp_path / "cache.json"

    save_resolution_cache(markets, str(cache_path))
    loaded = load_resolution_cache(str(cache_path))

    assert loaded == markets


@patch("trading.data_modules.resolution.requests.Session.get")
def test_fetch_resolved_markets_handles_request_error(mock_get) -> None:
    mock_get.side_effect = requests.RequestException("boom")

    assert fetch_resolved_markets(limit=1, offset=0) == []
