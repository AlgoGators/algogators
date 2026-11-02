"""
Tests for src.collection modules.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock

from src.collection.base import BaseCollector, CollectionResult
from src.collection.prices import PricePoint, PriceCollector
from src.collection.trades import Trade, TradeCollector


class TestCollectionResult:
    """Tests for CollectionResult."""
    
    def test_collection_result_duration(self):
        """Test duration_seconds property."""
        start = datetime(2026, 1, 1, 0, 0, 0)
        end = datetime(2026, 1, 1, 0, 0, 5)
        
        result = CollectionResult(
            success=True,
            records_fetched=10,
            records_stored=10,
            start_time=start,
            end_time=end,
        )
        
        assert result.duration_seconds == 5.0
    
    def test_collection_result_with_errors(self):
        """Test CollectionResult with errors."""
        result = CollectionResult(
            success=False,
            records_fetched=5,
            records_stored=0,
            start_time=datetime(2026, 1, 1),
            end_time=datetime(2026, 1, 1),
            errors=["Error 1", "Error 2"],
        )
        
        assert result.success is False
        assert len(result.errors) == 2


class TestBaseCollector:
    """Tests for BaseCollector."""
    
    def test_base_collector_init(self, tmp_path):
        """Test BaseCollector initialization."""
        test_dir = tmp_path / "test"
        collector = MockBaseCollector(name="test_collector", output_dir=str(test_dir))
        
        assert collector.name == "test_collector"
        assert str(collector.output_dir) == str(test_dir)
        assert collector.logger is not None
    
    def test_base_collector_init_defaults(self):
        """Test BaseCollector with default values."""
        collector = MockBaseCollector()
        
        assert collector.name == "collector"
        assert str(collector.output_dir) == "data"
    
    def test_ensure_output_dir(self, tmp_path):
        """Test ensure_output_dir creates directory."""
        collector = MockBaseCollector(output_dir=str(tmp_path / "new_dir"))
        
        result = collector.ensure_output_dir()
        
        assert result.exists()
        assert result.is_dir()
    
    def test_collect_success(self):
        """Test collect() with successful fetch and store."""
        import asyncio
        
        collector = MockBaseCollector()
        original_fetch = collector.fetch
        original_store = collector.store
        
        async def mock_fetch(**kwargs):
            return [1, 2, 3]
        
        def mock_store(data, **kwargs):
            return len(data)
        
        collector.fetch = mock_fetch
        collector.store = mock_store
        
        result = asyncio.run(collector.collect())
        
        assert result.success is True
        assert result.records_fetched == 3
        assert result.records_stored == 3
        assert len(result.errors) == 0
    
    def test_collect_with_exception(self):
        """Test collect() handles exceptions."""
        import asyncio
        
        collector = MockBaseCollector()
        
        async def mock_fetch(**kwargs):
            raise Exception("Fetch failed")
        
        collector.fetch = mock_fetch
        
        result = asyncio.run(collector.collect())
        
        assert result.success is False
        assert result.records_fetched == 0
        assert result.records_stored == 0
        assert len(result.errors) > 0
    
    def test_collect_empty_data(self):
        """Test collect() with empty fetch result."""
        import asyncio
        
        collector = MockBaseCollector()
        
        async def mock_fetch(**kwargs):
            return []
        
        def mock_store(data, **kwargs):
            return 0
        
        collector.fetch = mock_fetch
        collector.store = mock_store
        
        result = asyncio.run(collector.collect())
        
        assert result.success is True
        assert result.records_fetched == 0
        assert result.records_stored == 0


class MockBaseCollector(BaseCollector):
    """Mock collector for testing."""
    
    async def fetch(self, **kwargs):
        return []
    
    def store(self, data, **kwargs):
        return len(data)


class TestPriceCollector:
    """Tests for PriceCollector."""
    
    def test_price_collector_init(self, tmp_path):
        """Test PriceCollector initialization."""
        test_dir = tmp_path / "test"
        collector = PriceCollector(data_dir=str(test_dir), source="polymarket")
        
        assert collector.name == "prices_polymarket"
        assert collector.source == "polymarket"
        assert collector._fetcher is not None
    
    def test_price_point(self):
        """Test PricePoint dataclass."""
        point = PricePoint(
            market_id="market1",
            token_id="token1",
            timestamp=datetime(2026, 1, 1),
            price=0.55,
            outcome="YES",
        )
        
        assert point.market_id == "market1"
        assert point.price == 0.55
        assert point.outcome == "YES"
    
    def test_store(self):
        """Test PriceCollector.store()."""
        collector = PriceCollector()
        points = [
            PricePoint(
                market_id="m1",
                token_id="t1",
                timestamp=datetime.now(),
                price=0.5,
                outcome="YES",
            ),
            PricePoint(
                market_id="m2",
                token_id="t2",
                timestamp=datetime.now(),
                price=0.6,
                outcome="YES",
            ),
        ]
        
        result = collector.store(points)
        assert result == 2


class TestTradeCollector:
    """Tests for TradeCollector."""
    
    def test_trade_collector_init(self, tmp_path):
        """Test TradeCollector initialization."""
        test_dir = tmp_path / "test"
        collector = TradeCollector(data_dir=str(test_dir), source="polymarket")
        
        assert collector.name == "trades_polymarket"
        assert collector.source == "polymarket"
        assert collector._fetcher is not None
    
    def test_trade_dataclass(self):
        """Test Trade dataclass."""
        trade = Trade(
            trade_id="trade1",
            market_id="market1",
            timestamp=datetime(2026, 1, 1),
            maker="maker1",
            taker="taker1",
            price=0.55,
            usd_amount=100.0,
            token_amount=181.82,
            side="BUY",
        )
        
        assert trade.trade_id == "trade1"
        assert trade.price == 0.55
        assert trade.side == "BUY"
    
    def test_store(self):
        """Test TradeCollector.store()."""
        collector = TradeCollector()
        trades = [
            Trade(
                trade_id="t1",
                market_id="m1",
                timestamp=datetime.now(),
                maker="m1",
                taker="t1",
                price=0.5,
                usd_amount=100.0,
                token_amount=200.0,
                side="BUY",
            ),
        ]
        
        result = collector.store(trades)
        assert result == 1
