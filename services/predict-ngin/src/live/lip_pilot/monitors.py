"""Kill switches. Any trip -> the runner cancels every order and stops.

Triggers: daily loss, drawdown, total loss (prereg $30), unexpected position / orphan or foreign orders,
API errors (consecutive and per 10 min), stale book, clock skew, exchange inactive, and three
competition monitors:
- pennying: a side re-priced more than N times within a window (someone keeps stepping ahead of us);
- queue-ahead: others' size strictly above our price grows by >= Target/5 versus when we joined, and
  stays that way (the audit's "rival at the best bid": reward -57%);
- CUSUM markout: one-sided CUSUM of adverse per-contract markout of our fills at a horizon
  (S = max(0, S + adverse - k); trip when S > h). docs/CAPFWD_RUNNER.md does not exist,
  so k/h are set from the lipgas audit (BB1 fills -14.8c/contract at settlement).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

from .config import KillCfg


class KillTriggered(RuntimeError):
    def __init__(self, reason: str, detail: Optional[dict] = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


@dataclass
class _PendingMarkout:
    ticker: str
    outcome: str
    px: float
    count: float
    due: float


@dataclass
class KillSwitch:
    cfg: KillCfg
    tripped: Optional[str] = None
    detail: dict = field(default_factory=dict)
    consecutive_errors: int = 0
    error_times: Deque[float] = field(default_factory=deque)
    start_equity: Optional[float] = None
    day_start_equity: Dict[str, float] = field(default_factory=dict)
    peak_equity: Optional[float] = None
    reprices: Dict[Tuple[str, str], Deque[float]] = field(default_factory=dict)
    queue_baseline: Dict[Tuple[str, str], float] = field(default_factory=dict)
    queue_breach_since: Dict[Tuple[str, str], float] = field(default_factory=dict)
    cusum: float = 0.0
    pending: List[_PendingMarkout] = field(default_factory=list)

    # ---------------------------------------------------------------- core
    def trip(self, reason: str, **detail) -> None:
        if self.tripped is None:
            self.tripped = reason
            self.detail = detail

    def raise_if_tripped(self) -> None:
        if self.tripped:
            raise KillTriggered(self.tripped, self.detail)

    # ---------------------------------------------------------------- API health
    def api_ok(self) -> None:
        self.consecutive_errors = 0

    def api_error(self, now: float, what: str = "") -> None:
        self.consecutive_errors += 1
        self.error_times.append(now)
        while self.error_times and now - self.error_times[0] > 600:
            self.error_times.popleft()
        if self.consecutive_errors >= self.cfg.max_consecutive_api_errors:
            self.trip("api_errors_consecutive", count=self.consecutive_errors, last=what)
        elif len(self.error_times) >= self.cfg.max_api_errors_per_10min:
            self.trip("api_errors_rate", count=len(self.error_times), last=what)

    # ---------------------------------------------------------------- data / clock
    def check_book_age(self, ticker: str, book_ts: Optional[float], now: float) -> None:
        if book_ts is None or now - book_ts > self.cfg.stale_book_s:
            self.trip("stale_book", ticker=ticker, age_s=None if book_ts is None else round(now - book_ts, 1))

    def check_clock(self, skew_s: Optional[float]) -> None:
        if skew_s is not None and abs(skew_s) > self.cfg.max_clock_skew_s:
            self.trip("clock_skew", skew_s=round(skew_s, 2))

    # ---------------------------------------------------------------- P&L
    def check_pnl(self, equity: float, day: str) -> None:
        if self.start_equity is None:
            self.start_equity = equity
        self.day_start_equity.setdefault(day, equity)
        self.peak_equity = equity if self.peak_equity is None else max(self.peak_equity, equity)
        if self.day_start_equity[day] - equity > self.cfg.max_daily_loss_usd:
            self.trip("daily_loss", loss=round(self.day_start_equity[day] - equity, 2))
        elif self.peak_equity - equity > self.cfg.max_drawdown_usd:
            self.trip("drawdown", dd=round(self.peak_equity - equity, 2))
        elif self.start_equity - equity > self.cfg.max_total_loss_usd:
            self.trip("total_loss", loss=round(self.start_equity - equity, 2))

    # ---------------------------------------------------------------- positions
    def check_positions(self, expected_net: Dict[str, float], actual_net: Dict[str, float],
                        baseline: Dict[str, float]) -> None:
        tol = self.cfg.position_tolerance_contracts
        for tk in set(expected_net) | set(actual_net) | set(baseline):
            exp = expected_net.get(tk, baseline.get(tk, 0.0))
            act = actual_net.get(tk, 0.0)
            if abs(act - exp) > tol + 1e-9:
                self.trip("unexpected_position", ticker=tk, expected=exp, actual=act)
                return

    # ---------------------------------------------------------------- competition
    def on_reprice(self, ticker: str, outcome: str, now: float) -> None:
        q = self.reprices.setdefault((ticker, outcome), deque())
        q.append(now)
        while q and now - q[0] > self.cfg.pennying_window_s:
            q.popleft()
        if len(q) > self.cfg.pennying_max_reprices:
            self.trip("pennying", ticker=ticker, side=outcome, reprices=len(q))

    def on_queue_ahead(self, ticker: str, outcome: str, ahead: float, target: float, now: float) -> None:
        key = (ticker, outcome)
        base = self.queue_baseline.setdefault(key, ahead)
        if ahead - base >= self.cfg.queue_ahead_fraction_of_target * target - 1e-9:
            since = self.queue_breach_since.setdefault(key, now)
            if now - since >= self.cfg.queue_ahead_persist_s:
                self.trip("queue_ahead", ticker=ticker, side=outcome, ahead=ahead, baseline=base)
        else:
            self.queue_breach_since.pop(key, None)

    def on_fill(self, ticker: str, outcome: str, px: float, count: float, now: float) -> None:
        self.pending.append(_PendingMarkout(ticker, outcome, px, count, now + self.cfg.cusum_markout_horizon_s))

    def update_markouts(self, now: float, mid_yes: Callable[[str], Optional[float]]) -> List[dict]:
        done, keep = [], []
        for p in self.pending:
            if now < p.due:
                keep.append(p)
                continue
            m = mid_yes(p.ticker)
            if m is None:
                keep.append(p)
                continue
            value = m if p.outcome == "yes" else 1.0 - m
            markout = value - p.px                      # +: good for us
            adverse = -markout
            self.cusum = max(0.0, self.cusum + adverse - self.cfg.cusum_k)
            done.append({"ticker": p.ticker, "side": p.outcome, "px": p.px, "count": p.count,
                         "markout": round(markout, 4), "cusum": round(self.cusum, 4)})
            if self.cusum > self.cfg.cusum_h:
                self.trip("cusum_markout", cusum=round(self.cusum, 4), ticker=p.ticker)
        self.pending = keep
        return done
