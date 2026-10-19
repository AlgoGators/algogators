"""
LiveTradeBuffer — incremental, stateful real-time equivalent of
`extract_confirmed_signals` (confirmed_signals.py).

Instead of processing a full historical DataFrame, this class accepts one trade
at a time and maintains the rolling state needed for the 6-step confirmed-whale
filter chain.  Signals are returned immediately when confirmation conditions
are crossed.

Filter chain (mirrors confirmed_signals.py exactly):
  1. Size          : usd_amount >= min_size_usd
  2. Whale set     : maker in whale_set
  3. Category      : optional allowlist
  4. Wallet WR     : whale_winrates[maker] >= min_wallet_wr
  5. Price impact  : usd_amount / market_liquidity <= max_price_impact
  6. Confirmation  : >= min_confirmations distinct whales on same
                     (market, direction) within confirmation_window_hours
                     (with per-(market, direction) cooldown)

A signal dict is returned on the trade that crosses the confirmation threshold.
Signal format is compatible with PaperTrader.process_signal() and the
orchestrator's calculate_position_size() path.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple


class LiveTradeBuffer:
    """
    Stateful incremental confirmation buffer for live whale signal detection.

    Thread-safe: all public methods acquire _lock before modifying state,
    so it can safely be called from a WebSocket callback thread while a
    monitor thread reads state.

    Parameters
    ----------
    whale_set : set of lowercase maker addresses that qualify as whales
    whale_winrates : address -> historical win rate (0-1)
    market_liquidity : conditionId -> market volume/liquidity in USD
    whale_scores : address -> whale score (used in returned signal; defaults to 7.0)
    allowed_categories : if given, only trades in these categories pass step 3
    min_size_usd : step 1 — minimum single-trade size in USD
    min_wallet_wr : step 4 — minimum whale historical win-rate
    max_price_impact : step 5 — max fraction of market volume per trade
    min_confirmations : step 6 — distinct whales needed within window
    confirmation_window_hours : rolling look-back for step 6
    cooldown_hours : silence repeated signals for same (market, direction)
    max_entry_yes_price : skip entry when YES price > this (near-resolved filter)
    max_spread : skip entry when best_ask - best_bid > this (illiquid market gate)
    state_path : if set, persist/restore buffer state here for restart safety
    """

    def __init__(
        self,
        whale_set: Set[str],
        whale_winrates: Dict[str, float],
        market_liquidity: Dict[str, float],
        whale_scores: Optional[Dict[str, float]] = None,
        allowed_categories: Optional[List[str]] = None,
        min_size_usd: float = 1_000.0,
        min_wallet_wr: float = 0.55,
        max_price_impact: float = 0.10,
        min_confirmations: int = 1,
        confirmation_window_hours: int = 168,
        cooldown_hours: int = 168,
        max_entry_yes_price: float = 0.98,
        max_spread: float = 0.10,
        state_path: Optional[Path] = None,
    ):
        self.whale_set = {a.lower() for a in whale_set}
        self.whale_winrates = {a.lower(): v for a, v in whale_winrates.items()}
        self.whale_scores = {a.lower(): v for a, v in (whale_scores or {}).items()}
        self.market_liquidity = market_liquidity
        self.allowed_categories_lower = (
            {c.lower() for c in allowed_categories} if allowed_categories else None
        )
        self.min_size_usd = min_size_usd
        self.min_wallet_wr = min_wallet_wr
        self.max_price_impact = max_price_impact
        self.min_confirmations = min_confirmations
        self.window_td = timedelta(hours=confirmation_window_hours)
        self.cooldown_td = timedelta(hours=cooldown_hours)
        self.max_entry_yes_price = max_entry_yes_price
        self.max_spread = max_spread
        self.state_path = state_path

        # Rolling window: (market_id, direction) -> deque of (datetime, maker)
        self._window: Dict[Tuple[str, str], Deque[Tuple[datetime, str]]] = \
            defaultdict(deque)
        # Last signal time: (market_id, direction) -> datetime
        self._last_signal: Dict[Tuple[str, str], datetime] = {}
        # Counters
        self.trades_seen = 0
        self.trades_passed_filter = 0
        self.signals_emitted = 0

        self._lock = threading.Lock()

        if state_path and Path(state_path).exists():
            self._load_state()

    # ── Public API ─────────────────────────────────────────────────────────────

    def add(self, trade: dict) -> List[dict]:
        """
        Process one normalised trade dict.

        Returns a (possibly empty) list of signal dicts.  Each signal is
        immediately ready for PaperTrader.process_signal() or OrderRouter.

        Expected trade fields:
            market_id   : conditionId (str)
            maker       : wallet address (str, will be lowercased)
            direction   : "BUY" or "SELL" (or "buy"/"sell")
            price       : YES token price, 0-1 (float)
            usd_amount  : trade size in USD (float)
            datetime    : aware or naive datetime
            category    : category string (optional)
            token_id    : CLOB token ID (optional; forwarded to signal)
            spread      : live bid-ask spread (optional; 0 if unknown)
        """
        with self._lock:
            self.trades_seen += 1

            mid       = str(trade.get("market_id", "") or "").strip()
            maker     = str(trade.get("maker", "") or "").lower().strip()
            direction = str(trade.get("direction", "BUY") or "BUY").upper()
            price     = float(trade.get("price", 0) or 0)
            usd       = float(trade.get("usd_amount", 0) or 0)
            category  = str(trade.get("category", "") or "")
            token_id  = str(trade.get("token_id", "") or "")
            spread    = float(trade.get("spread", 0) or 0)
            ts        = trade.get("datetime")
            if ts is None:
                ts = datetime.now(timezone.utc)
            elif not isinstance(ts, datetime):
                try:
                    ts = datetime.fromisoformat(str(ts))
                except Exception:
                    ts = datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            if not mid or not maker:
                return []

            # Step 1: size
            if usd < self.min_size_usd:
                return []

            # Step 1b: near-resolved guard
            if price > self.max_entry_yes_price:
                return []

            # Step 1c: spread gate
            if self.max_spread > 0 and spread > self.max_spread:
                return []

            # Step 2: whale set
            if self.whale_set and maker not in self.whale_set:
                return []

            # Step 3: category allowlist
            if self.allowed_categories_lower and category.lower() not in self.allowed_categories_lower:
                return []

            # Step 4: wallet win-rate
            wr = self.whale_winrates.get(maker, 0.0)
            if wr < self.min_wallet_wr:
                return []

            # Step 5: price impact
            liq = float(self.market_liquidity.get(mid, 0) or 0)
            if liq > 0:
                impact = usd / liq
                if impact > self.max_price_impact:
                    return []

            self.trades_passed_filter += 1

            # Step 6: confirmation window
            key = (mid, direction)
            now_ts = ts

            # Enforce cooldown
            last = self._last_signal.get(key)
            if last is not None and (now_ts - last) < self.cooldown_td:
                self._window[key].append((now_ts, maker))
                return []

            # Prune stale entries
            cutoff = now_ts - self.window_td
            win = self._window[key]
            while win and win[0][0] < cutoff:
                win.popleft()

            # Count distinct prior whales (excluding current maker)
            prior_whales = {m for _, m in win if m != maker}

            if len(prior_whales) >= (self.min_confirmations - 1):
                all_whales = prior_whales | {maker}
                score = self.whale_scores.get(maker, 7.0)

                signal = {
                    "market_id":          mid,
                    "direction":          direction.lower(),
                    "price":              price,
                    "usd_amount":         usd,
                    "token_id":           token_id,
                    "category":           category,
                    "whale_address":      maker,
                    "whale_score":        score,
                    "whale_winrate":      wr,
                    "confirming_whales":  len(all_whales),
                    "whale_addresses":    sorted(all_whales),
                    "market_liquidity":   liq,
                    "trigger_ts":         now_ts.isoformat(),
                    "source":             "confirmed_whale",
                }

                self._last_signal[key] = now_ts
                self._window[key].clear()
                self._window[key].append((now_ts, maker))
                self.signals_emitted += 1

                if self.state_path:
                    self._save_state()

                return [signal]
            else:
                win.append((now_ts, maker))
                return []

    def update_whale_set(
        self,
        whale_set: Set[str],
        whale_winrates: Dict[str, float],
        whale_scores: Optional[Dict[str, float]] = None,
    ) -> None:
        """Replace the whale set in-place (called on weekly refresh)."""
        with self._lock:
            self.whale_set = {a.lower() for a in whale_set}
            self.whale_winrates = {a.lower(): v for a, v in whale_winrates.items()}
            if whale_scores is not None:
                self.whale_scores = {a.lower(): v for a, v in whale_scores.items()}

    def update_market_liquidity(self, market_liquidity: Dict[str, float]) -> None:
        """Replace market liquidity map in-place."""
        with self._lock:
            self.market_liquidity = market_liquidity

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "trades_seen":          self.trades_seen,
                "trades_passed_filter": self.trades_passed_filter,
                "signals_emitted":      self.signals_emitted,
                "active_windows":       len(self._window),
                "whale_count":          len(self.whale_set),
            }

    # ── State persistence ──────────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Persist rolling window + cooldown state to JSON for restart safety."""
        if not self.state_path:
            return
        try:
            path = Path(self.state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "window": {
                    f"{k[0]}|{k[1]}": [[t.isoformat(), m] for t, m in v]
                    for k, v in self._window.items()
                },
                "last_signal": {
                    f"{k[0]}|{k[1]}": v.isoformat()
                    for k, v in self._last_signal.items()
                },
            }
            with open(path, "w") as f:
                json.dump(state, f)
        except Exception:
            pass

    def _load_state(self) -> None:
        """Restore rolling window + cooldown state from JSON."""
        try:
            with open(self.state_path) as f:
                state = json.load(f)
            for raw_key, entries in state.get("window", {}).items():
                mid, direction = raw_key.split("|", 1)
                key = (mid, direction)
                for ts_str, maker in entries:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    self._window[key].append((ts, maker))
            for raw_key, ts_str in state.get("last_signal", {}).items():
                mid, direction = raw_key.split("|", 1)
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                self._last_signal[(mid, direction)] = ts
        except Exception:
            pass
