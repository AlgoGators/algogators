"""Market selection by the frozen lipscan/lipx rule, under hard caps.

1. Universe: active ``liquidity`` incentive programmes (public GET /incentive_programs) whose market is in
   a ``KXAAAGAS*`` series (lipscan F1), restricted to the lipx rule-G daily-state allowlist when set.
2. The market is open and has >= min_minutes_to_close left (so it quotes past the 60-min pull).
3. Rank by programme pool $/day (lipscan prereg section 4 capacity ranking), tie-break by the modelled
   reward per $ of max loss at the current book.
4. Keep markets where at least one side gets a quote under the posture and caps; at most one market per
   series (state); at most ``max_markets``; skip markets with pre-existing positions or resting orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

from .book import Book
from .config import PilotConfig
from .posture import Program, Quote, desired_quote
from .risk import MarketExposure
from .scoring import reward_rate_usd_per_s, snapshot_score


def parse_ts(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# Shim (step 5.5): series parsing lives in predict_ngin.markets.tickers.
from predict_ngin.markets.tickers import series_of  # noqa: E402,F401


def parse_programs(raw: Iterable[dict], cfg: PilotConfig, now: float) -> Dict[str, Program]:
    """One Program per market ticker. Overlapping programmes on one market are scored separately by
    Kalshi; we keep the one with the highest pool rate for quoting and log the others."""
    sel = cfg.selection
    allow = set(sel.series_allowlist or [])
    out: Dict[str, Program] = {}
    for p in raw:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        tk = p.get("market_ticker") or ""
        if not tk.startswith(sel.series_prefix):
            continue
        if allow and series_of(tk) not in allow:
            continue
        a, b = parse_ts(p.get("start_date")), parse_ts(p.get("end_date"))
        if a is None or b is None or not (a <= now < b):
            continue
        tgt = p.get("target_size_fp") if p.get("target_size_fp") is not None else p.get("target_size")
        try:
            target = float(tgt)
        except (TypeError, ValueError):
            continue
        dfb = p.get("discount_factor_bps")
        df = float(dfb) / 1e4 if dfb is not None else 0.5
        usd = float(p.get("period_reward") or 0) / 1e4          # centi-cents -> USD
        prog = Program(str(p.get("id") or ""), tk, target, df, usd, a, b)
        if tk not in out or prog.reward_usd_day > out[tk].reward_usd_day:
            out[tk] = prog
    return out


@dataclass
class Candidate:
    prog: Program
    close_ts: float
    book: Book
    yes: Quote
    no: Quote
    max_loss: float
    est_usd_per_s: float
    rank_key: tuple = field(default_factory=tuple)

    @property
    def reward_per_dollar(self) -> float:
        return self.est_usd_per_s / self.max_loss if self.max_loss > 0 else 0.0


def evaluate(prog: Program, book: Book, close_ts: float, cfg: PilotConfig, room_total: float) -> Candidate:
    exp = MarketExposure()
    cap = cfg.caps.max_per_market_at_risk_usd
    qy = desired_quote(book, "yes", prog, cfg.posture, exp, None, 0, None, 0, cap, room_total, 0)
    if qy.px is not None:
        exp.yes_resting, exp.yes_px = qy.size, qy.px
    qn = desired_quote(book, "no", prog, cfg.posture, exp, None, 0, None, 0, cap, room_total, 0)
    if qn.px is not None:
        exp.no_resting, exp.no_px = qn.size, qn.px
    snap, excluded, _, _ = snapshot_score(
        book.yes, book.no, prog.target, prog.df,
        (qy.px, qy.size) if qy.px is not None else None,
        (qn.px, qn.size) if qn.px is not None else None, cfg.posture.tick)
    rate = 0.0 if excluded else reward_rate_usd_per_s(prog.period_reward_usd, prog.period_s, snap)
    return Candidate(prog, close_ts, book, qy, qn, exp.max_loss(cfg.posture.maker_fee_per_contract), rate)


def select_markets(cands: List[Candidate], cfg: PilotConfig, exclude: Set[str] = frozenset()) -> List[Candidate]:
    ok = [c for c in cands if c.prog.ticker not in exclude and (c.yes.px is not None or c.no.px is not None)
          and c.est_usd_per_s > 0]
    ok.sort(key=lambda c: (-round(c.prog.reward_usd_day, 2), -c.reward_per_dollar, c.prog.ticker))
    chosen: List[Candidate] = []
    seen_series: Set[str] = set()
    total = 0.0
    n_max = min(cfg.caps.max_markets, cfg.selection.max_markets)
    for c in ok:
        if len(chosen) >= n_max:
            break
        s = series_of(c.prog.ticker)
        if s in seen_series:
            continue
        if c.max_loss > cfg.caps.max_per_market_at_risk_usd + 1e-9:
            continue
        if total + c.max_loss > cfg.caps.max_total_at_risk_usd + 1e-9:
            continue
        chosen.append(c)
        seen_series.add(s)
        total += c.max_loss
    return chosen
