# Production Orchestrator — Implementation Guide

## Goal

Build a single script `scripts/live/run_live_strategy.py` that is the missing
bridge between the existing backtest research pipeline and live/paper trading.

When complete, running this script will:
1. Load historical trade data and build the whale set + win rates (offline warmup)
2. Subscribe to the Polymarket WebSocket for live trades
3. Apply the confirmed-whale filter chain to every incoming trade in real time
4. Forward confirmed signals to `PaperTrader.process_signal()` (or live orders)
5. Enforce a risk kill-switch that pauses trading on drawdown breach
6. Log everything to the existing execution logger

---

## System Architecture

```
[WebSocket: wss://ws-subscriptions-clob.polymarket.com/ws/market]
        |
        v
[LiveTradeBuffer]  — rolling window of recent trades per (market, direction)
        |
        v
[ConfirmedWhaleFilter]  — the 6-step pipeline from confirmed_signals.py
        |  needs: whale_set, whale_winrates, market_liquidity (pre-loaded)
        v
[SignalRouter]  — dedup, cooldown, risk-limit check
        |
        v
[PaperTrader.process_signal()] or [OrderRouter] for live
        |
        v
[ExecutionLogger + PositionMonitor]
```

---

## What Already Exists (Do NOT Rewrite)

### Signal generation — offline (backtest)
- `src/whale_strategy/whale_surprise.py`
  - `identify_whales_rolling(trades_df, ...)` → adds `is_whale` column
  - `build_surprise_positive_whale_set(train_trades, resolution_winners, ...)` →
    returns `(whale_set: Set[str], whale_scores: Dict[str, float], whale_winrates: Dict[str, float])`

- `src/whale_strategy/confirmed_signals.py`
  - `extract_confirmed_signals(trades_df, whale_set, whale_winrates, market_liquidity, ...)` →
    DataFrame of confirmed signals. This function is **batch-only** (processes a full
    DataFrame). The orchestrator must maintain a rolling trade buffer and call it
    periodically, or reimplement the confirmation logic incrementally.

### Real-time WebSocket
- `src/trading/live/realtime_signals.py`
  - `WhaleSignalGenerator(whale_addresses, min_usd_threshold, on_signal=callback)`
    — simple address-based monitor, fires on any whale address trade
  - `WhaleSignal` dataclass: `signal_type`, `market_id`, `token_id`, `maker`,
    `trade_side`, `usd_amount`, `price`, `confidence`
  - The WebSocket pipeline lives in `PolymarketWebSocket` (same file). It handles
    reconnects with exponential backoff.
  - **Important**: This fires on ANY whale trade, no confirmation or price-impact
    checks. It is NOT the confirmed-whale pipeline. The orchestrator either
    replaces this with its own handler or wraps it.

### Paper trading engine
- `src/trading/live/paper_trading.py`
  - `PaperTrader(initial_capital, state_path, log_path, max_position_size, max_positions)`
  - **Key method**: `PaperTrader.process_signal(signal: dict)` where signal is:
    ```python
    {
        "market_id": "0xabc...",   # conditionId
        "direction": "buy",         # "buy" or "sell"
        "size_usd": 300.0,
        "source": "confirmed_whale",
    }
    ```
  - State persists to `data/paper_trading_state.json`
  - Log goes to `data/paper_trading_log.jsonl`

### Position monitoring
- `src/trading/live/position_monitor.py`
  - `PositionMonitor` — reads paper trading state, fires alerts on drawdown
  - Does NOT halt trading automatically. The orchestrator must poll it and pause
    signal forwarding when risk limits are breached.

### Data loading
- `src/whale_strategy/research_data_loader.py`
  - `load_research_trades(research_dir, categories, min_usd)` → DataFrame
  - `load_research_markets(research_dir, categories)` → DataFrame
  - `load_resolution_winners(research_dir)` → Dict[market_id, "YES"|"NO"]
  - `get_research_categories(research_dir)` → List[str]

---

## What Needs To Be Built

### `scripts/live/run_live_strategy.py`

#### Phase 1 — Warmup (offline, ~minutes)
```python
# 1. Load historical trades for whale identification
trades = load_research_trades(research_dir, categories=CATEGORIES)
resolutions = load_resolution_winners(research_dir)

# 2. Build whale set and win rates from training window
#    Use the most recent N months as training data
train_trades = trades[trades["datetime"] >= cutoff]
whale_set, whale_scores, whale_winrates = build_surprise_positive_whale_set(
    train_trades, resolutions,
    min_surprise=0.0, min_trades=10, min_actual_win_rate=0.60
)

# 3. Build market liquidity map from markets_filtered.csv
markets_df = load_research_markets(research_dir, categories=CATEGORIES)
market_liquidity = dict(zip(markets_df["market_id"], markets_df["liquidityNum"]))

# 4. Pre-populate rolling trade buffer with recent trades
#    (so confirmation logic has context from the start)
recent_trades = trades[trades["datetime"] >= (now - timedelta(hours=WINDOW_HOURS))]
# Load into LiveTradeBuffer (see below)
```

#### Phase 2 — Live loop
```python
# 5. Start paper trader
trader = PaperTrader(initial_capital=CAPITAL, ...)

# 6. Start WebSocket, inject trades into buffer
async def on_trade(trade_dict):
    buffer.add(trade_dict)
    signals = buffer.check_confirmations(whale_set, whale_winrates, market_liquidity)
    for sig in signals:
        if not risk_paused:
            trader.process_signal(sig)

# 7. Periodically check drawdown and pause if breached
def risk_check():
    equity = trader.account.equity()
    drawdown = (CAPITAL - equity) / CAPITAL
    if drawdown > MAX_DRAWDOWN:
        risk_paused = True
        log("RISK PAUSE: drawdown {drawdown:.1%}")
```

#### `LiveTradeBuffer` class (needs to be written)
This is the core new component. It maintains a rolling window of recent trades
per `(market_id, direction)` and checks confirmation conditions.

```python
class LiveTradeBuffer:
    """
    Maintains a rolling window of live trades and checks the confirmed-whale
    pipeline incrementally (stateful, real-time equivalent of extract_confirmed_signals).
    """
    def __init__(
        self,
        whale_set: Set[str],
        whale_winrates: Dict[str, float],
        market_liquidity: Dict[str, float],
        min_size_usd: float = 10_000,
        min_market_volume: float = 500_000,
        min_wallet_wr: float = 0.60,
        max_price_impact: float = 0.05,
        min_confirmations: int = 2,
        confirmation_window_hours: int = 24,
        cooldown_hours: int = 168,
        allowed_categories: Optional[List[str]] = None,
    ):
        ...

    def add(self, trade: dict) -> None:
        """Add a normalised trade dict to the buffer."""
        ...

    def check_confirmations(self) -> List[dict]:
        """
        After each add(), check if a new confirmation threshold has been crossed.
        Returns list of signal dicts ready for PaperTrader.process_signal().
        Handles cooldowns internally.
        """
        ...
```

Internal state the buffer needs:
- `_window: Dict[(market_id, direction), deque[trade]]` — trades within window
- `_cooldowns: Dict[(market_id, direction), datetime]` — last signal time
- Prune old trades from window on each `add()`

Signal dict the buffer returns (matches `PaperTrader.process_signal` format):
```python
{
    "market_id": "0xabc...",
    "direction": "buy",
    "size_usd": 300.0,          # 3% of capital, capped at $50K
    "source": "confirmed_whale",
    "confirming_whales": ["0x1...", "0x2..."],
    "n_confirmations": 2,
    "trigger_price": 0.42,
}
```

---

## Key Parameters (use these defaults, make them CLI args)

| Parameter | Default | Notes |
|---|---|---|
| `--research-dir` | `data/poly_cat` | Where trades/markets/resolutions live |
| `--categories` | `Politics,Geopolitics` | Comma-separated |
| `--capital` | `10000` | Paper trading starting capital (USD) |
| `--train-months` | `6` | How many months back for whale identification |
| `--min-size-usd` | `10000` | Step 1: minimum single trade size |
| `--min-market-volume` | `500000` | Step 2: market liquidity floor |
| `--min-wallet-wr` | `0.60` | Step 4: whale win-rate threshold |
| `--max-price-impact` | `0.05` | Step 5: max fraction of market volume |
| `--min-confirmations` | `2` | Step 6: distinct whales needed |
| `--window-hours` | `24` | Confirmation look-back window |
| `--cooldown-hours` | `168` | 1 week silence after signal |
| `--max-drawdown` | `0.15` | Risk kill-switch at 15% drawdown |
| `--position-size-pct` | `0.03` | 3% of capital per trade |
| `--max-position-usd` | `50000` | Hard cap per position |

---

## Confirmed-Whale Filter Chain (reference)

The 6 steps live in `src/whale_strategy/confirmed_signals.py::extract_confirmed_signals`.
The `LiveTradeBuffer` must reimplement these incrementally:

1. **Size**: `trade["usd_amount"] >= min_size_usd`
2. **Liquidity**: `market_liquidity.get(market_id, 0) >= min_market_volume`
3. **Category**: `trade["category"] in allowed_categories` (if set)
4. **Wallet WR**: `whale_winrates.get(trader, 0) >= min_wallet_wr`
5. **Price impact**: `trade["usd_amount"] / market_liquidity[market_id] <= max_price_impact`
6. **Confirmation**: `len(distinct_whales_in_window(market_id, direction)) >= min_confirmations`

A trade that passes all 6 AND the trader is in `whale_set` triggers a signal.

---

## WebSocket Trade Format

Trades arriving from the Polymarket WebSocket look like:
```python
{
    "id": "...",
    "timestamp": 1709000000,     # unix seconds
    "market": "0xabc...",        # conditionId = market_id
    "asset_id": "...",           # token_id
    "maker_address": "0x...",
    "taker_address": "0x...",
    "side": "BUY",               # or "SELL"
    "price": "0.42",
    "size": "714.28",            # token amount
    "usd_amount": "300.0",       # may need to compute: price * size
}
```

Normalise to internal format before passing to buffer:
```python
{
    "market_id":   trade["market"],
    "maker":       trade["maker_address"].lower(),
    "side":        trade["side"].upper(),      # "BUY" or "SELL"
    "price":       float(trade["price"]),
    "usd_amount":  float(trade.get("usd_amount") or float(trade["price"]) * float(trade["size"])),
    "datetime":    datetime.fromtimestamp(int(trade["timestamp"]), tz=timezone.utc),
    "category":    market_to_category.get(trade["market"], ""),
}
```

---

## File Layout After Implementation

```
scripts/live/
    run_live_strategy.py      ← NEW: main orchestrator
    __init__.py               ← NEW: empty

src/trading/live/
    paper_trading.py          ← EXISTS: PaperTrader.process_signal()
    realtime_signals.py       ← EXISTS: WebSocket connection
    position_monitor.py       ← EXISTS: risk monitoring
    order_router.py           ← EXISTS: for eventual live orders
    execution_logger.py       ← EXISTS: trade logging
```

---

## Suggested Implementation Order

1. Write `LiveTradeBuffer` class with `add()` and `check_confirmations()` methods
2. Write warmup phase (load trades, build whale set, build market_liquidity map)
3. Wire WebSocket feed → buffer using existing `realtime_signals.py` connection
4. Wire buffer outputs → `PaperTrader.process_signal()`
5. Add risk kill-switch (poll `trader.account` equity, pause on breach)
6. Add CLI args and logging
7. Test with `--dry-run` (log signals without passing to trader)

---

## How to Test Without Live Data

Run a replay test using historical trades as if they arrived live:
```bash
python scripts/live/run_live_strategy.py \
  --replay \
  --research-dir data/poly_cat \
  --categories Politics,Geopolitics \
  --capital 10000
```

The `--replay` flag should iterate over `trades.parquet` in chronological order,
feeding each trade into the buffer at simulated speed rather than connecting to
the WebSocket. This lets you validate the signal pipeline matches the backtest
results before going live.

---

## Related Scripts for Reference

- **Backtest** (shows end-to-end pipeline in batch mode):
  `scripts/backtest/run_confirmed_whale_backtest.py`
- **Whale identification** (shows how warmup parameters interact):
  `scripts/backtest/run_whale_full_sweep.py`
- **Paper trader CLI** (run standalone):
  `python -m src.trading.live.paper_trading --start`
- **Dashboard** (monitor paper trader state):
  `python scripts/monitoring/dashboard.py --live`

---

## Enabling Live Orders (after paper trading validates)

1. Set env vars: `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`
2. Set `config/local.yaml`: `live_trading.enabled: true`, `dry_run: false`
3. In orchestrator, replace `PaperTrader.process_signal()` with
   `OrderRouter.submit_order()` from `src/trading/live/order_router.py`
4. Keep `PositionMonitor` and `ExecutionLogger` wired regardless of paper/live mode
