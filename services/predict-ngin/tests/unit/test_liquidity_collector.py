"""Unit tests for LiquidityCollector and LiquidityStore."""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

import pandas as pd

from src.collection.liquidity import LiquiditySnapshot, LiquidityCollector
from src.storage.liquidity import LiquidityStore


@pytest.fixture
def temp_data_dir():
    """Create a temporary directory for data."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestLiquiditySnapshot:
    def test_from_orderbook(self):
        bids = [(0.45, 1000), (0.44, 500), (0.43, 200)]
        asks = [(0.55, 800), (0.56, 600), (0.57, 300)]

        snapshot = LiquiditySnapshot.from_orderbook(
            market_id="test_market",
            token_id="token_123",
            bids=bids,
            asks=asks,
        )

        assert snapshot.market_id == "test_market"
        assert snapshot.token_id == "token_123"
        assert snapshot.best_bid == pytest.approx(0.45)
        assert snapshot.best_ask == pytest.approx(0.55)
        assert snapshot.spread == pytest.approx(0.10)
        assert snapshot.mid_price == pytest.approx(0.50)
        assert snapshot.total_bid_depth == pytest.approx(1700)
        assert snapshot.total_ask_depth == pytest.approx(1700)

    def test_from_orderbook_empty(self):
        snapshot = LiquiditySnapshot.from_orderbook(
            market_id="empty_market",
            token_id="token_456",
            bids=[],
            asks=[],
        )

        assert snapshot.mid_price == pytest.approx(0.5)
        assert snapshot.spread == pytest.approx(0.0)
        assert snapshot.total_bid_depth == pytest.approx(0)

    def test_to_dict(self):
        snapshot = LiquiditySnapshot.from_orderbook(
            market_id="test",
            token_id="token",
            bids=[(0.5, 100)],
            asks=[(0.6, 100)],
        )

        d = snapshot.to_dict()

        assert "market_id" in d
        assert "timestamp" in d
        assert "mid_price" in d
        assert "spread" in d


class TestLiquidityStore:
    def test_init(self, temp_data_dir):
        store = LiquidityStore(base_dir=temp_data_dir)

        assert store.base_dir.exists()

    def test_append_and_load(self, temp_data_dir):
        store = LiquidityStore(base_dir=temp_data_dir)

        snapshots = [
            LiquiditySnapshot.from_orderbook(
                market_id=f"market_{i}",
                token_id=f"token_{i}",
                bids=[(0.5, 100)],
                asks=[(0.6, 100)],
                timestamp=datetime(2025, 1, 15, 12, i),
            )
            for i in range(3)
        ]

        stored = store.append(snapshots)
        assert stored == 3

        loaded = store.load()
        assert len(loaded) == 3
        assert "market_id" in loaded.columns

    def test_load_with_filter(self, temp_data_dir):
        store = LiquidityStore(base_dir=temp_data_dir)

        snapshots = [
            LiquiditySnapshot.from_orderbook(
                market_id="market_A",
                token_id="token_A",
                bids=[(0.5, 100)],
                asks=[(0.6, 100)],
            ),
            LiquiditySnapshot.from_orderbook(
                market_id="market_B",
                token_id="token_B",
                bids=[(0.4, 200)],
                asks=[(0.7, 200)],
            ),
        ]

        store.append(snapshots)

        loaded = store.load(market_id="market_A")
        assert len(loaded) == 1
        assert loaded.iloc[0]["market_id"] == "market_A"

    def test_list_months(self, temp_data_dir):
        store = LiquidityStore(base_dir=temp_data_dir)

        # Create snapshots for different months
        for month in [1, 2, 3]:
            snapshot = LiquiditySnapshot.from_orderbook(
                market_id="test",
                token_id="token",
                bids=[(0.5, 100)],
                asks=[(0.6, 100)],
                timestamp=datetime(2025, month, 15),
            )
            store.append([snapshot])

        months = store.list_months()
        assert len(months) == 3
        assert "2025-01" in months

    def test_available(self, temp_data_dir):
        store = LiquidityStore(base_dir=temp_data_dir)

        assert not store.available()

        snapshot = LiquiditySnapshot.from_orderbook(
            market_id="test",
            token_id="token",
            bids=[(0.5, 100)],
            asks=[(0.6, 100)],
        )
        store.append([snapshot])

        assert store.available()


class TestLiquidityCollector:
    def test_init(self, temp_data_dir):
        collector = LiquidityCollector(output_dir=temp_data_dir)

        assert collector.output_dir.exists()

    def test_store_fallback(self, temp_data_dir):
        collector = LiquidityCollector(output_dir=temp_data_dir)

        snapshots = [
            LiquiditySnapshot.from_orderbook(
                market_id="test",
                token_id="token",
                bids=[(0.5, 100)],
                asks=[(0.6, 100)],
            )
        ]

        stored = collector.store(snapshots)
        assert stored == 1
