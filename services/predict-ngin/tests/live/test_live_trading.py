"""
Comprehensive tests for live trading modules.

Tests the following modules:
- PaperTrader (paper_trading.py)
- OrderRouter (order_router.py)
- SmartOrderRouter

All tests use mocks to avoid real API calls.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from trading.live.paper_trading import (
    PaperTrader,
    PaperAccount,
    PaperOrder,
    PaperPosition,
    CostModel,
    PriceTracker,
    SignalQueue,
    OrderSide,
    OrderStatus,
    PositionStatus,
)
from trading.live.order_router import (
    LiveTradingDisabledError,
    OrderRouter,
    OrderBook,
    OrderResult,
    OrderSide as RouterOrderSide,
    OrderStatus as RouterOrderStatus,
    SmartOrderRouter,
)


# =============================================================================
# CostModel Tests
# =============================================================================

class TestCostModel:
    """Tests for CostModel class."""

    def test_default_initialization(self):
        """Test default initialization values."""
        model = CostModel()

        assert model.base_spread == 0.02
        assert model.slippage_bps == 50
        assert model.impact_coef == 0.5
        assert model.fee_rate == 0.001

    def test_custom_initialization(self):
        """Test custom initialization values."""
        model = CostModel(
            base_spread=0.03,
            slippage_bps=100,
            impact_coef=0.8,
            fee_rate=0.002,
        )

        assert model.base_spread == 0.03
        assert model.slippage_bps == 100
        assert model.impact_coef == 0.8
        assert model.fee_rate == 0.002

    def test_calculate_execution_price_buy(self):
        """Test execution price calculation for buy orders."""
        model = CostModel(
            base_spread=0.02,
            slippage_bps=50,
            impact_coef=0.5,
            fee_rate=0.001,
        )

        exec_price, slippage, fees = model.calculate_execution_price(
            side=OrderSide.BUY,
            mid_price=0.50,
            size_usd=100,
            liquidity=10000,
        )

        # Buy price should be higher than mid price
        assert exec_price > 0.50
        assert slippage > 0
        assert fees == 100 * 0.001

    def test_calculate_execution_price_sell(self):
        """Test execution price calculation for sell orders."""
        model = CostModel(
            base_spread=0.02,
            slippage_bps=50,
            impact_coef=0.5,
            fee_rate=0.001,
        )

        exec_price, slippage, fees = model.calculate_execution_price(
            side=OrderSide.SELL,
            mid_price=0.50,
            size_usd=100,
            liquidity=10000,
        )

        # Sell price should be lower than mid price
        assert exec_price < 0.50
        assert slippage > 0
        assert fees == 100 * 0.001

    def test_larger_orders_have_more_impact(self):
        """Test that larger orders have more market impact."""
        model = CostModel()

        _, small_slip, _ = model.calculate_execution_price(
            side=OrderSide.BUY,
            mid_price=0.50,
            size_usd=100,
            liquidity=10000,
        )

        _, large_slip, _ = model.calculate_execution_price(
            side=OrderSide.BUY,
            mid_price=0.50,
            size_usd=5000,
            liquidity=10000,
        )

        assert large_slip > small_slip

    def test_low_liquidity_increases_impact(self):
        """Test that low liquidity increases market impact."""
        model = CostModel()

        _, high_liq_slip, _ = model.calculate_execution_price(
            side=OrderSide.BUY,
            mid_price=0.50,
            size_usd=1000,
            liquidity=50000,
        )

        _, low_liq_slip, _ = model.calculate_execution_price(
            side=OrderSide.BUY,
            mid_price=0.50,
            size_usd=1000,
            liquidity=1000,
        )

        assert low_liq_slip > high_liq_slip

    def test_zero_liquidity_handling(self):
        """Test handling of zero liquidity."""
        model = CostModel()

        exec_price, slippage, fees = model.calculate_execution_price(
            side=OrderSide.BUY,
            mid_price=0.50,
            size_usd=100,
            liquidity=0,
        )

        # Should apply high default impact
        assert slippage >= 0.05


# =============================================================================
# PriceTracker Tests
# =============================================================================

class TestPriceTracker:
    """Tests for PriceTracker class."""

    def test_update_and_get_price(self):
        """Test updating and retrieving prices."""
        tracker = PriceTracker()

        tracker.update("market_1", 0.55)
        tracker.update("market_2", 0.45)

        assert tracker.get("market_1") == 0.55
        assert tracker.get("market_2") == 0.45

    def test_get_nonexistent_market(self):
        """Test getting price for nonexistent market."""
        tracker = PriceTracker()

        result = tracker.get("nonexistent")
        assert result is None

    def test_get_all_prices(self):
        """Test getting all prices."""
        tracker = PriceTracker()

        tracker.update("m1", 0.50)
        tracker.update("m2", 0.60)
        tracker.update("m3", 0.40)

        all_prices = tracker.get_all()

        assert len(all_prices) == 3
        assert all_prices["m1"] == 0.50
        assert all_prices["m2"] == 0.60
        assert all_prices["m3"] == 0.40

    def test_update_overwrites_price(self):
        """Test that updating overwrites previous price."""
        tracker = PriceTracker()

        tracker.update("m1", 0.50)
        tracker.update("m1", 0.55)

        assert tracker.get("m1") == 0.55

    def test_last_update_time_tracked(self):
        """Test that last update time is tracked."""
        tracker = PriceTracker()

        before = datetime.now()
        tracker.update("m1", 0.50)
        after = datetime.now()

        assert "m1" in tracker.last_update
        assert before <= tracker.last_update["m1"] <= after


# =============================================================================
# SignalQueue Tests
# =============================================================================

class TestSignalQueue:
    """Tests for SignalQueue class."""

    def test_put_and_get_signal(self):
        """Test putting and getting signals."""
        queue = SignalQueue()

        signal = {"market_id": "m1", "direction": "buy", "size_usd": 100}
        queue.put(signal)

        retrieved = queue.get(timeout=1.0)

        assert retrieved["market_id"] == "m1"
        assert "received_at" in retrieved

    def test_empty_queue_returns_none(self):
        """Test that empty queue returns None."""
        queue = SignalQueue()

        result = queue.get(timeout=0.1)
        assert result is None

    def test_queue_fifo_order(self):
        """Test that queue maintains FIFO order."""
        queue = SignalQueue()

        queue.put({"id": 1})
        queue.put({"id": 2})
        queue.put({"id": 3})

        assert queue.get(timeout=0.1)["id"] == 1
        assert queue.get(timeout=0.1)["id"] == 2
        assert queue.get(timeout=0.1)["id"] == 3

    def test_empty_check(self):
        """Test empty() method."""
        queue = SignalQueue()

        assert queue.empty() is True

        queue.put({"id": 1})
        assert queue.empty() is False


# =============================================================================
# PaperAccount Tests
# =============================================================================

class TestPaperAccount:
    """Tests for PaperAccount class."""

    def test_default_initialization(self):
        """Test default account initialization."""
        account = PaperAccount()

        assert account.initial_capital == 10000.0
        assert account.cash == 10000.0
        assert account.total_pnl == 0
        assert account.total_fees == 0
        assert account.trade_count == 0
        assert account.win_count == 0

    def test_custom_initialization(self):
        """Test custom account initialization."""
        account = PaperAccount(initial_capital=50000.0, cash=50000.0)

        assert account.initial_capital == 50000.0
        assert account.cash == 50000.0

    def test_equity_calculation(self):
        """Test equity calculation with positions."""
        account = PaperAccount(initial_capital=10000, cash=8000)

        # Add a position with unrealized P/L
        position = PaperPosition(
            position_id="pos_1",
            market_id="m1",
            token_id="t1",
            outcome="YES",
            side=OrderSide.BUY,
            entry_price=0.50,
            entry_size_usd=2000,
            entry_time=datetime.now(),
            current_price=0.55,
            unrealized_pnl=100,
            status=PositionStatus.OPEN,
        )
        account.positions["pos_1"] = position

        assert account.equity == 8000 + 100  # cash + unrealized

    def test_win_rate_calculation(self):
        """Test win rate calculation."""
        account = PaperAccount()
        account.trade_count = 10
        account.win_count = 6

        assert account.win_rate == 0.6

    def test_win_rate_zero_trades(self):
        """Test win rate with zero trades."""
        account = PaperAccount()

        assert account.win_rate == 0

    def test_to_dict_serialization(self):
        """Test account serialization to dict."""
        account = PaperAccount(initial_capital=10000, cash=9500)
        account.total_pnl = 500
        account.trade_count = 5

        data = account.to_dict()

        assert data["initial_capital"] == 10000
        assert data["cash"] == 9500
        assert data["total_pnl"] == 500
        assert data["trade_count"] == 5

    def test_from_dict_deserialization(self):
        """Test account deserialization from dict."""
        data = {
            "initial_capital": 20000,
            "cash": 18000,
            "total_pnl": 1000,
            "total_fees": 50,
            "trade_count": 10,
            "win_count": 7,
            "positions": {},
            "orders": {},
            "created_at": "2026-01-01T00:00:00",
        }

        account = PaperAccount.from_dict(data)

        assert account.initial_capital == 20000
        assert account.cash == 18000
        assert account.total_pnl == 1000
        assert account.trade_count == 10


# =============================================================================
# PaperTrader Tests
# =============================================================================

class TestPaperTrader:
    """Tests for PaperTrader class."""

    @pytest.fixture
    def trader(self, tmp_path: Path) -> PaperTrader:
        """Create a PaperTrader for testing."""
        trader = PaperTrader(
            initial_capital=10000.0,
            db_path=str(tmp_path / "test.db"),
            state_path=str(tmp_path / "state.json"),
            log_path=str(tmp_path / "log.jsonl"),
            max_position_size=1000.0,
            max_positions=10,
        )
        # Mock API calls
        trader.get_market_price = MagicMock(return_value=0.50)
        trader.get_market_liquidity = MagicMock(return_value=5000)
        return trader

    def test_initialization(self, trader: PaperTrader):
        """Test trader initialization."""
        assert trader.account.initial_capital == 10000.0
        assert trader.max_position_size == 1000.0
        assert trader.max_positions == 10

    def test_submit_buy_order(self, trader: PaperTrader):
        """Test submitting a buy order."""
        order = trader.submit_order(
            market_id="test_market",
            token_id="test_token",
            side=OrderSide.BUY,
            size_usd=500,
            signal_source="test",
        )

        assert order is not None
        assert order.status == OrderStatus.FILLED
        assert order.side == OrderSide.BUY
        assert order.filled_size == 500

        # Check account updated
        assert trader.account.cash < 10000.0
        assert len(trader.account.positions) == 1

    def test_submit_sell_order(self, trader: PaperTrader):
        """Test submitting a sell order."""
        order = trader.submit_order(
            market_id="test_market",
            token_id="test_token",
            side=OrderSide.SELL,
            size_usd=500,
            signal_source="test",
        )

        assert order is not None
        assert order.status == OrderStatus.FILLED
        assert order.side == OrderSide.SELL

        # Cash should increase for sells
        assert trader.account.cash > 10000.0

    def test_order_exceeds_max_size(self, trader: PaperTrader):
        """Test that orders exceeding max size are rejected."""
        order = trader.submit_order(
            market_id="test_market",
            token_id="test_token",
            side=OrderSide.BUY,
            size_usd=5000,  # Exceeds max of 1000
            signal_source="test",
        )

        assert order is None

    def test_order_exceeds_cash(self, trader: PaperTrader):
        """Test that orders exceeding available cash are rejected."""
        trader.account.cash = 100  # Low cash

        order = trader.submit_order(
            market_id="test_market",
            token_id="test_token",
            side=OrderSide.BUY,
            size_usd=500,  # Exceeds cash
            signal_source="test",
        )

        assert order is None

    def test_max_positions_limit(self, trader: PaperTrader):
        """Test that max positions limit is enforced."""
        trader.max_positions = 2

        # Open two positions
        trader.submit_order("m1", "t1", OrderSide.BUY, 100, "test")
        trader.submit_order("m2", "t2", OrderSide.BUY, 100, "test")

        # Third should be rejected
        order = trader.submit_order("m3", "t3", OrderSide.BUY, 100, "test")

        assert order is None

    def test_close_position(self, trader: PaperTrader):
        """Test closing a position."""
        # Open a position
        trader.submit_order("m1", "t1", OrderSide.BUY, 500, "test")

        position_id = list(trader.account.positions.keys())[0]
        position = trader.account.positions[position_id]

        # Close it
        close_order = trader.close_position(position_id)

        assert close_order is not None
        assert position.status == PositionStatus.CLOSED
        assert position.exit_price is not None

    def test_close_nonexistent_position(self, trader: PaperTrader):
        """Test closing a nonexistent position."""
        result = trader.close_position("nonexistent_id")
        assert result is None

    def test_update_positions(self, trader: PaperTrader):
        """Test updating position P/L."""
        # Open a position at 0.50
        trader.submit_order("m1", "t1", OrderSide.BUY, 500, "test")

        position_id = list(trader.account.positions.keys())[0]

        # Price increased to 0.55
        trader.get_market_price = MagicMock(return_value=0.55)
        trader.update_positions()

        position = trader.account.positions[position_id]
        assert position.current_price == 0.55
        assert position.unrealized_pnl > 0  # Should be positive

    def test_get_status(self, trader: PaperTrader):
        """Test getting trading status."""
        trader.submit_order("m1", "t1", OrderSide.BUY, 500, "test")

        status = trader.get_status()

        assert "equity" in status
        assert "cash" in status
        assert "open_positions" in status
        assert status["open_positions"] == 1

    def test_process_signal(self, trader: PaperTrader):
        """Test processing a trading signal."""
        signal = {
            "market_id": "signal_market",
            "token_id": "signal_token",
            "direction": "buy",
            "size_usd": 200,
            "source": "test_strategy",
        }

        trader.process_signal(signal)

        assert len(trader.account.positions) == 1

    def test_process_signal_no_duplicate_position(self, trader: PaperTrader):
        """Test that signals don't create duplicate positions."""
        # Open initial position
        trader.submit_order("m1", "t1", OrderSide.BUY, 200, "test")

        # Try to process signal for same market
        signal = {
            "market_id": "m1",
            "token_id": "t1",
            "direction": "buy",
            "size_usd": 200,
            "source": "test",
        }

        trader.process_signal(signal)

        # Should still have only one position
        assert len(trader.account.positions) == 1

    def test_reset_account(self, trader: PaperTrader):
        """Test resetting the account."""
        # Make some trades
        trader.submit_order("m1", "t1", OrderSide.BUY, 500, "test")
        trader.account.total_pnl = 1000

        # Reset
        trader.reset(initial_capital=20000)

        assert trader.account.initial_capital == 20000
        assert trader.account.cash == 20000
        assert trader.account.total_pnl == 0
        assert len(trader.account.positions) == 0

    def test_state_persistence(self, trader: PaperTrader, tmp_path: Path):
        """Test that state is saved and loaded correctly."""
        # Make a trade
        trader.submit_order("m1", "t1", OrderSide.BUY, 500, "test")

        # Save state
        trader._save_state()

        # Load state in new trader
        new_trader = PaperTrader(
            initial_capital=10000.0,
            db_path=str(tmp_path / "test.db"),
            state_path=str(tmp_path / "state.json"),
            log_path=str(tmp_path / "log.jsonl"),
        )

        # Should have loaded the position
        assert len(new_trader.account.positions) == 1

    def test_order_id_generation(self, trader: PaperTrader):
        """Test that order IDs are unique."""
        id1 = trader._generate_order_id()
        id2 = trader._generate_order_id()

        assert id1 != id2
        assert id1.startswith("PAPER-")

    def test_position_id_generation(self, trader: PaperTrader):
        """Test that position IDs are unique."""
        id1 = trader._generate_position_id()
        id2 = trader._generate_position_id()

        assert id1 != id2
        assert id1.startswith("POS-")


# =============================================================================
# OrderRouter Tests
# =============================================================================

class TestOrderRouter:
    """Tests for OrderRouter class."""

    def test_dry_run_initialization(self):
        """Test initialization in dry run mode."""
        router = OrderRouter(dry_run=True)

        assert router.dry_run is True

    # Live Polymarket trading is hard-disabled (plan step 7, 2026-10-15): the
    # former live-auth tests (rewritten 2026-10-15 to the env-var contract) were
    # replaced by these. Dry-run still counts as authenticated.
    def test_live_mode_is_disabled(self):
        """OrderRouter(dry_run=False) raises and never builds a CLOB client."""
        with patch("trading.live.order_router._build_clob_client") as build:
            with pytest.raises(LiveTradingDisabledError, match="disabled"):
                OrderRouter(dry_run=False)
        build.assert_not_called()

    def test_live_disabled_error_is_runtime_error(self):
        assert issubclass(LiveTradingDisabledError, RuntimeError)

    def test_dry_run_is_authenticated(self):
        assert OrderRouter(dry_run=True).is_authenticated() is True

    def test_place_market_order_dry_run(self):
        """Test placing market order in dry run mode."""
        router = OrderRouter(dry_run=True)

        # Mock the order book call
        router.estimate_fill_price = MagicMock(return_value=(0.52, 0.01))

        result = router.place_market_order(
            token_id="test_token",
            side=RouterOrderSide.BUY,
            size_usd=100,
        )

        assert result.success is True
        assert result.order_id.startswith("DRY-")
        assert result.status == "filled"
        assert result.raw_response["dry_run"] is True

    def test_place_limit_order_dry_run(self):
        """Test placing limit order in dry run mode."""
        router = OrderRouter(dry_run=True)

        result = router.place_limit_order(
            token_id="test_token",
            side=RouterOrderSide.BUY,
            price=0.48,
            size_usd=100,
        )

        assert result.success is True
        assert result.order_id.startswith("DRY-LMT-")
        assert result.status == "live"

    def test_cancel_order_dry_run(self):
        """Test canceling order in dry run mode."""
        router = OrderRouter(dry_run=True)

        # Place an order first
        result = router.place_limit_order("token", RouterOrderSide.BUY, 0.50, 100)

        # Cancel it
        cancelled = router.cancel_order(result.order_id)

        assert cancelled is True
        assert result.order_id not in router.pending_orders

    def test_cancel_all_orders_dry_run(self):
        """Test canceling all orders in dry run mode."""
        router = OrderRouter(dry_run=True)

        # Place multiple orders
        router.place_limit_order("t1", RouterOrderSide.BUY, 0.50, 100)
        router.place_limit_order("t2", RouterOrderSide.BUY, 0.50, 100)
        router.place_limit_order("t3", RouterOrderSide.BUY, 0.50, 100)

        # Cancel all
        cancelled = router.cancel_all_orders()

        assert cancelled == 3
        assert len(router.pending_orders) == 0

    def test_get_open_orders_dry_run(self):
        """Test getting open orders in dry run mode."""
        router = OrderRouter(dry_run=True)

        router.place_limit_order("t1", RouterOrderSide.BUY, 0.50, 100)
        router.place_limit_order("t2", RouterOrderSide.SELL, 0.55, 100)

        orders = router.get_open_orders()

        assert len(orders) == 2

    def test_get_fills_dry_run(self):
        """Test getting fills in dry run mode."""
        router = OrderRouter(dry_run=True)

        fills = router.get_fills()

        assert isinstance(fills, list)

    @patch("trading.live.order_router.requests.Session.get")
    def test_get_order_book(self, mock_get, mock_order_book_response: Dict[str, Any]):
        """Test getting order book."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_order_book_response

        router = OrderRouter(dry_run=True)
        book = router.get_order_book("test_token")

        assert book is not None
        assert isinstance(book, OrderBook)
        assert book.best_bid == 0.48
        assert book.best_ask == 0.52
        assert book.spread == 0.04

    @patch("trading.live.order_router.requests.Session.get")
    def test_get_best_price_buy(self, mock_get, mock_order_book_response: Dict[str, Any]):
        """Test getting best price for buy."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_order_book_response

        router = OrderRouter(dry_run=True)
        price = router.get_best_price("test_token", RouterOrderSide.BUY)

        # Buy should get ask price
        assert price == 0.52

    @patch("trading.live.order_router.requests.Session.get")
    def test_get_best_price_sell(self, mock_get, mock_order_book_response: Dict[str, Any]):
        """Test getting best price for sell."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_order_book_response

        router = OrderRouter(dry_run=True)
        price = router.get_best_price("test_token", RouterOrderSide.SELL)

        # Sell should get bid price
        assert price == 0.48

    @patch("trading.live.order_router.requests.Session.get")
    def test_get_midpoint(self, mock_get, mock_order_book_response: Dict[str, Any]):
        """Test getting midpoint price."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_order_book_response

        router = OrderRouter(dry_run=True)
        midpoint = router.get_midpoint("test_token")

        assert midpoint == 0.50

    @patch("trading.live.order_router.requests.Session.get")
    def test_estimate_fill_price(self, mock_get, mock_order_book_response: Dict[str, Any]):
        """Test estimating fill price with book walking."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_order_book_response

        router = OrderRouter(dry_run=True)
        avg_price, slippage = router.estimate_fill_price(
            "test_token",
            RouterOrderSide.BUY,
            500,  # Should take some of first level
        )

        assert avg_price > 0
        assert slippage >= 0


# =============================================================================
# ClobAuth Tests
# =============================================================================

@pytest.mark.skip(reason="stale: ClobAuth no longer exists in trading.live.order_router; kept for reference")
class TestClobAuth:
    """Tests for ClobAuth class."""

    def test_sign_request(self):
        """Test request signing."""
        auth = ClobAuth(
            api_key="test_key",
            api_secret="test_secret",
            passphrase="test_pass",
        )

        headers = auth.sign_request("POST", "/order", '{"test": "data"}')

        assert "POLY-API-KEY" in headers
        assert headers["POLY-API-KEY"] == "test_key"
        assert "POLY-PASSPHRASE" in headers
        assert headers["POLY-PASSPHRASE"] == "test_pass"
        assert "POLY-TIMESTAMP" in headers
        assert "POLY-SIGNATURE" in headers

    def test_sign_request_deterministic(self):
        """Test that signing is deterministic with same timestamp."""
        auth = ClobAuth(
            api_key="test_key",
            api_secret="test_secret",
            passphrase="test_pass",
        )

        timestamp = "1234567890"
        headers1 = auth.sign_request("POST", "/order", '{"test": "data"}', timestamp)
        headers2 = auth.sign_request("POST", "/order", '{"test": "data"}', timestamp)

        assert headers1["POLY-SIGNATURE"] == headers2["POLY-SIGNATURE"]

    def test_different_methods_different_signatures(self):
        """Test that different methods produce different signatures."""
        auth = ClobAuth(
            api_key="test_key",
            api_secret="test_secret",
            passphrase="test_pass",
        )

        timestamp = "1234567890"
        headers_get = auth.sign_request("GET", "/order", "", timestamp)
        headers_post = auth.sign_request("POST", "/order", "", timestamp)

        assert headers_get["POLY-SIGNATURE"] != headers_post["POLY-SIGNATURE"]


# =============================================================================
# SmartOrderRouter Tests
# =============================================================================

class TestSmartOrderRouter:
    """Tests for SmartOrderRouter class."""

    @pytest.fixture
    def smart_router(self) -> SmartOrderRouter:
        """Create a SmartOrderRouter for testing."""
        base_router = OrderRouter(dry_run=True)
        base_router.estimate_fill_price = MagicMock(return_value=(0.52, 0.01))
        return SmartOrderRouter(base_router)

    def test_execute_twap(self, smart_router: SmartOrderRouter):
        """Test TWAP execution."""
        # Use very short intervals for testing
        results = smart_router.execute_twap(
            token_id="test_token",
            side=RouterOrderSide.BUY,
            total_size_usd=100,
            duration_minutes=0.01,  # Very short
            num_slices=2,
        )

        assert len(results) == 2
        assert all(r.success for r in results)

    def test_execute_iceberg(self, smart_router: SmartOrderRouter):
        """Test iceberg execution."""
        results = smart_router.execute_iceberg(
            token_id="test_token",
            side=RouterOrderSide.BUY,
            total_size_usd=300,
            visible_size_usd=100,
        )

        assert len(results) == 3  # 300 / 100 = 3 slices
        assert all(r.success for r in results)


# =============================================================================
# OrderBook Tests
# =============================================================================

class TestOrderBook:
    """Tests for OrderBook dataclass."""

    def test_order_book_creation(self):
        """Test OrderBook creation."""
        book = OrderBook(
            token_id="test_token",
            timestamp=datetime.now(),
            bids=[(0.48, 1000), (0.47, 2000)],
            asks=[(0.52, 1000), (0.53, 2000)],
            best_bid=0.48,
            best_ask=0.52,
            spread=0.04,
            midpoint=0.50,
        )

        assert book.token_id == "test_token"
        assert book.best_bid == 0.48
        assert book.best_ask == 0.52
        assert book.spread == 0.04
        assert book.midpoint == 0.50


# =============================================================================
# OrderResult Tests
# =============================================================================

class TestOrderResult:
    """Tests for OrderResult dataclass."""

    def test_successful_order_result(self):
        """Test successful order result."""
        result = OrderResult(
            success=True,
            order_id="order_123",
            status="filled",
            filled_size=100,
            avg_price=0.52,
            fees=0.10,
        )

        assert result.success is True
        assert result.order_id == "order_123"
        assert result.error is None

    def test_failed_order_result(self):
        """Test failed order result."""
        result = OrderResult(
            success=False,
            error="Insufficient funds",
        )

        assert result.success is False
        assert result.error == "Insufficient funds"
        assert result.order_id is None


# =============================================================================
# Integration Tests
# =============================================================================

class TestPaperTradingIntegration:
    """Integration tests for paper trading flow."""

    @pytest.fixture
    def trader(self, tmp_path: Path) -> PaperTrader:
        """Create a PaperTrader for testing."""
        trader = PaperTrader(
            initial_capital=10000.0,
            db_path=str(tmp_path / "test.db"),
            state_path=str(tmp_path / "state.json"),
            log_path=str(tmp_path / "log.jsonl"),
            max_position_size=1000.0,
            max_positions=10,
        )
        trader.get_market_price = MagicMock(return_value=0.50)
        trader.get_market_liquidity = MagicMock(return_value=5000)
        return trader

    def test_full_trade_cycle(self, trader: PaperTrader):
        """Test a complete buy -> hold -> sell cycle."""
        # 1. Open position
        buy_order = trader.submit_order(
            market_id="m1",
            token_id="t1",
            side=OrderSide.BUY,
            size_usd=500,
            signal_source="test",
        )
        assert buy_order is not None

        position_id = list(trader.account.positions.keys())[0]
        initial_cash = trader.account.cash

        # 2. Simulate price increase
        trader.get_market_price = MagicMock(return_value=0.60)
        trader.update_positions()

        position = trader.account.positions[position_id]
        assert position.unrealized_pnl > 0

        # 3. Close position
        sell_order = trader.close_position(position_id)
        assert sell_order is not None

        # 4. Verify P/L
        assert position.status == PositionStatus.CLOSED
        assert trader.account.total_pnl > 0
        assert trader.account.trade_count == 1
        assert trader.account.win_count == 1

    def test_losing_trade(self, trader: PaperTrader):
        """Test a losing trade."""
        # Open position
        trader.submit_order("m1", "t1", OrderSide.BUY, 500, "test")
        position_id = list(trader.account.positions.keys())[0]

        # Price drops
        trader.get_market_price = MagicMock(return_value=0.40)
        trader.update_positions()

        position = trader.account.positions[position_id]
        assert position.unrealized_pnl < 0

        # Close at loss
        trader.close_position(position_id)

        assert trader.account.total_pnl < 0
        assert trader.account.win_count == 0

    def test_multiple_positions(self, trader: PaperTrader):
        """Test managing multiple positions."""
        # Open multiple positions
        trader.submit_order("m1", "t1", OrderSide.BUY, 200, "test")
        trader.submit_order("m2", "t2", OrderSide.BUY, 200, "test")
        trader.submit_order("m3", "t3", OrderSide.BUY, 200, "test")

        assert len(trader.account.positions) == 3

        status = trader.get_status()
        assert status["open_positions"] == 3

        # Close one
        pos_id = list(trader.account.positions.keys())[0]
        trader.close_position(pos_id)

        status = trader.get_status()
        assert status["open_positions"] == 2

    def test_event_logging(self, trader: PaperTrader, tmp_path: Path):
        """Test that events are logged."""
        trader.submit_order("m1", "t1", OrderSide.BUY, 500, "test")

        # Check log file exists and has content
        log_path = tmp_path / "log.jsonl"
        assert log_path.exists()

        with open(log_path) as f:
            lines = f.readlines()
            assert len(lines) > 0

            # Parse first log entry
            entry = json.loads(lines[0])
            assert "timestamp" in entry
            assert "type" in entry
