"""LIP pilot main loop.

    venv/Scripts/python.exe -m src.live.lip_pilot                 # dry run (default)
    venv/Scripts/python.exe -m src.live.lip_pilot --live          # live ONLY if all three gates hold

Every exit path (normal end, kill trigger, Ctrl+C / SIGTERM / SIGBREAK, exception) runs ``shutdown`` in a
``finally`` block, which cancels every resting order and, in live mode, verifies with a read-only GET that
none of ours remain.
"""
from __future__ import annotations

import argparse
import secrets
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .book import Book
from .client import ApiError, KalshiClient, MutationBlocked
from .config import LiveGate, PilotConfig, load_config, resolve_gate
from .journal import Journal
from .monitors import KillSwitch, KillTriggered
from .orders import ClientIdFactory, DryRunGateway, LiveGateway, OrderManager, PostOnlyRejected, is_pilot_id
from .posture import Program, desired_quote
from .ratelimit import TokenBucket
from .risk import MarketExposure
from .scoring import reward_rate_usd_per_s, snapshot_score
from .selection import Candidate, evaluate, parse_programs, parse_ts, select_markets

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class MarketState:
    prog: Program
    close_ts: float
    pull_ts: float
    book: Optional[Book] = None
    pulled: bool = False
    est_reward_usd: float = 0.0
    last_snapshot_ts: Optional[float] = None
    snapshots: int = 0
    excluded_snapshots: int = 0


class Pilot:
    def __init__(self, cfg: PilotConfig, gate: LiveGate, client: KalshiClient, journal: Journal,
                 run_id: Optional[str] = None, clock: Callable[[], float] = time.time,
                 account_reads: Optional[bool] = None):
        self.cfg = cfg
        self.gate = gate
        self.client = client
        self.journal = journal
        self.clock = clock
        self.live = gate.armed
        self.account_reads = self.live or (cfg.api.dry_run_account_reads if account_reads is None else account_reads)
        self.run_id = run_id or (datetime.now(timezone.utc).strftime("%y%m%d%H%M") + secrets.token_hex(3))
        self.ids = ClientIdFactory(self.run_id)
        self.ks = KillSwitch(cfg.kill)
        self.gw = LiveGateway(client, journal) if self.live else DryRunGateway(journal)
        self.om = OrderManager(self.gw, self.ids, journal, cfg.posture.min_reprice_interval_s,
                               on_reprice=self.ks.on_reprice)
        self.markets: Dict[str, MarketState] = {}
        self.baseline_positions: Dict[str, float] = {}
        self.seen_fills: set = set()
        self.start_ts = clock()
        self.last_reconcile = -1e18
        self.stop_requested: Optional[str] = None
        self._stop_event = threading.Event()
        self.shutdown_done = False

    # ------------------------------------------------------------------ helpers
    def request_stop(self, reason: str) -> None:
        if self.stop_requested is None:
            self.stop_requested = reason
        self._stop_event.set()

    def _call(self, what: str, fn, *a, **kw):
        """Run an exchange call, feeding the API-error kill switch. Re-raises nothing but MutationBlocked."""
        try:
            out = fn(*a, **kw)
        except MutationBlocked:
            raise
        except ApiError as e:
            self.ks.api_error(self.clock(), f"{what}: {e}")
            self.journal.write("api_error", what=what, status=e.status, error=str(e)[:300])
            return None
        except Exception as e:  # network / parse
            self.ks.api_error(self.clock(), f"{what}: {type(e).__name__}")
            self.journal.write("api_error", what=what, error=f"{type(e).__name__}: {str(e)[:200]}")
            return None
        self.ks.api_ok()
        self.ks.check_clock(self.client.last_clock_skew_s)
        return out

    def exposure(self, ticker: str) -> MarketExposure:
        e = MarketExposure()
        for outcome in ("yes", "no"):
            st = self.om.side(ticker, outcome)
            o = st.order
            if outcome == "yes":
                e.yes_filled, e.yes_cost = st.filled, st.cost
                if o is not None:
                    e.yes_resting, e.yes_px = o.size - o.filled, o.px
            else:
                e.no_filled, e.no_cost = st.filled, st.cost
                if o is not None:
                    e.no_resting, e.no_px = o.size - o.filled, o.px
        return e

    def total_max_loss(self, exclude: Optional[str] = None) -> float:
        fee = self.cfg.posture.maker_fee_per_contract
        return sum(self.exposure(t).max_loss(fee) for t in self.markets if t != exclude)

    def equity(self) -> float:
        """Cash P&L of pilot fills marked at the current best bid of each side (conservative)."""
        eq = 0.0
        for t, m in self.markets.items():
            for outcome in ("yes", "no"):
                st = self.om.side(t, outcome)
                bid = m.book.best_bid(outcome) if m.book else None
                eq += st.filled * (bid or 0.0) - st.cost
        return eq

    # ------------------------------------------------------------------ setup
    def setup(self) -> None:
        cfg = self.cfg
        self.journal.write("start", mode="LIVE" if self.live else "DRY_RUN", run_id=self.run_id,
                           gate={"cli_live": self.gate.cli_live, "env_ok": self.gate.env_ok,
                                 "config_live": self.gate.config_live}, gate_missing=self.gate.reasons(),
                           caps=vars(cfg.caps), kill=vars(cfg.kill), posture=vars(cfg.posture),
                           account_reads=self.account_reads)
        st = self._call("exchange_status", self.client.exchange_status)
        if st is not None and st.get("trading_active") is False:
            self.ks.trip("exchange_inactive", status=st)
        self.ks.raise_if_tripped()

        exclude = set()
        if self.account_reads:
            exclude = self.startup_reconcile()
        self.ks.raise_if_tripped()

        now = self.clock()
        raw = self._call("incentive_programs", self.client.incentive_programs, "active", "liquidity")
        if raw is None:
            self.ks.raise_if_tripped()
            raise KillTriggered("selection_failed", {"why": "incentive_programs unavailable"})
        progs = parse_programs(raw, cfg, now)
        ranked = sorted(progs.values(), key=lambda p: (-p.reward_usd_day, p.ticker))
        cands: List[Candidate] = []
        for p in ranked[: cfg.selection.max_candidates_books]:
            if p.ticker in exclude:
                continue
            mk = self._call("market", self.client.market, p.ticker)
            if not mk:
                continue
            close_ts = parse_ts(mk.get("close_time"))
            if mk.get("status") not in ("active", "open") or close_ts is None:
                continue
            if (close_ts - now) / 60.0 < cfg.selection.min_minutes_to_close:
                continue
            body = self._call("orderbook", self.client.orderbook, p.ticker)
            if body is None:
                continue
            book = Book.from_api(p.ticker, body, self.clock())
            c = evaluate(p, book, close_ts, cfg, cfg.caps.max_total_at_risk_usd)
            cands.append(c)
            self.journal.write("candidate", ticker=p.ticker, program_id=p.program_id, target=p.target, df=p.df,
                               pool_usd_day=round(p.reward_usd_day, 2), close_ts=close_ts,
                               yes=vars(c.yes), no=vars(c.no), max_loss=round(c.max_loss, 2),
                               est_usd_per_s=c.est_usd_per_s, reward_per_dollar=c.reward_per_dollar)
            self.ks.raise_if_tripped()
        chosen = select_markets(cands, cfg, exclude)
        for c in chosen:
            pull = c.close_ts - cfg.posture.pull_minutes_before_close * 60.0
            pull = min(pull, c.prog.end_ts)
            self.markets[c.prog.ticker] = MarketState(c.prog, c.close_ts, pull, c.book)
        self.journal.write("selected", tickers=list(self.markets), n_candidates=len(cands),
                           planned_max_loss=round(sum(c.max_loss for c in chosen), 2))
        if self.account_reads:
            for t in self.markets:
                if abs(self.baseline_positions.get(t, 0.0)) > 0:
                    raise KillTriggered("unexpected_position", {"ticker": t, "why": "pre-existing position"})

    def startup_reconcile(self) -> set:
        """Read-only GETs. Cancels leftover orders of earlier pilot runs (live only); returns tickers to skip."""
        exclude = set()
        pos = self._call("positions", self.client.get_positions) or []
        self.baseline_positions = {p.get("ticker"): float(p.get("position_fp") or p.get("position") or 0)
                                   for p in pos if p.get("ticker")}
        exclude |= {t for t, v in self.baseline_positions.items() if abs(v) > 0}
        orders = self._call("orders", self.client.get_orders, "resting")
        if orders is None:
            self.ks.trip("reconcile_failed", what="orders")
            return exclude
        leftovers = [o for o in orders if is_pilot_id(o.get("client_order_id"))]
        for o in orders:
            exclude.add(o.get("ticker"))
        self.journal.write("startup_reconcile", positions=len(self.baseline_positions),
                           resting=len(orders), pilot_leftovers=len(leftovers))
        if leftovers:
            if self.live:
                self._call("cancel_leftovers", self.client.batch_cancel,
                           [(o.get("order_id"), o.get("ticker")) for o in leftovers])
            else:
                for o in leftovers:
                    self.journal.write("would_cancel", order_id=o.get("order_id"), reason="leftover_from_prior_run")
        return exclude

    # ------------------------------------------------------------------ periodic reconcile (read-only)
    def reconcile(self, now: float) -> None:
        self.last_reconcile = now
        st = self._call("exchange_status", self.client.exchange_status)
        if st is not None and st.get("trading_active") is False:
            self.ks.trip("exchange_inactive", status=st)
        if not self.account_reads:
            return
        self.poll_fills(now)
        orders = self._call("orders", self.client.get_orders, "resting")
        if orders is not None:
            tracked = {o.order_id for o in self.om.resting()}
            exch = {o.get("order_id"): o for o in orders}
            for oid, o in exch.items():
                tk, cid = o.get("ticker"), o.get("client_order_id")
                if tk in self.markets and not self.ids.owns(cid):
                    self.ks.trip("foreign_order", ticker=tk, order_id=oid)
                elif self.ids.owns(cid) and oid not in tracked:
                    self.ks.trip("orphan_order", ticker=tk, order_id=oid)
            for (tk, outcome), side in list(self.om.sides.items()):
                mo = side.order
                if mo is None:
                    continue
                if mo.order_id not in exch:
                    self.journal.write("order_gone", ticker=tk, side=outcome, order_id=mo.order_id)
                    side.order = None   # filled or expired; fills are counted from /portfolio/fills
                else:
                    rem = float(exch[mo.order_id].get("remaining_count_fp") or 0)
                    self.om._record_fill(side, mo, max(mo.filled, mo.size - rem))
        pos = self._call("positions", self.client.get_positions)
        if pos is not None:
            actual = {p.get("ticker"): float(p.get("position_fp") or p.get("position") or 0) for p in pos
                      if p.get("ticker")}
            expected = dict(self.baseline_positions)
            for t in self.markets:
                expected[t] = (self.baseline_positions.get(t, 0.0) + self.om.side(t, "yes").filled
                               - self.om.side(t, "no").filled)
            self.ks.check_positions(expected, actual, self.baseline_positions)
        bal = self._call("balance", self.client.get_balance)
        self.journal.write("reconcile", resting=None if orders is None else len(orders),
                           balance=bal, est_reward_usd=round(sum(m.est_reward_usd for m in self.markets.values()), 4))

    def poll_fills(self, now: float) -> None:
        fills = self._call("fills", self.client.get_fills, int(self.start_ts) - 5)
        for f in fills or []:
            fid = f.get("fill_id") or f.get("trade_id")
            if fid in self.seen_fills:
                continue
            self.seen_fills.add(fid)
            tk = f.get("market_ticker") or f.get("ticker")
            outcome = f.get("outcome_side") or f.get("side")
            count = float(f.get("count_fp") or f.get("count") or 0)
            px = float((f.get("yes_price_dollars") if outcome == "yes" else f.get("no_price_dollars")) or 0)
            hit = self.om.apply_fill(f.get("order_id"), count)
            self.journal.write("fill", ticker=tk, side=outcome, count=count, px=px, order_id=f.get("order_id"),
                               is_taker=f.get("is_taker"), ours=hit is not None)
            if hit is None:
                if tk in self.markets:
                    self.ks.trip("unexpected_fill", ticker=tk, order_id=f.get("order_id"))
                continue
            if f.get("is_taker"):
                self.ks.trip("post_only_violation", ticker=tk, order_id=f.get("order_id"))
            self.ks.on_fill(tk, outcome, px, count, now)

    # ------------------------------------------------------------------ one loop iteration
    def step(self, now: Optional[float] = None) -> bool:
        """Returns False when there is nothing left to do."""
        now = self.clock() if now is None else now
        cfg = self.cfg
        active = [t for t, m in self.markets.items() if not m.pulled]
        for t in active:
            m = self.markets[t]
            if now >= m.pull_ts:
                self.om.sync(t, "yes", None, 0, now, None, "pull_T-60")
                self.om.sync(t, "no", None, 0, now, None, "pull_T-60")
                m.pulled = True
                self.journal.write("pulled", ticker=t, close_ts=m.close_ts)
                continue
            body = self._call("orderbook", self.client.orderbook, t)
            if body is not None:
                m.book = Book.from_api(t, body, now)
            self.ks.check_book_age(t, m.book.ts if m.book else None, now)
            if m.book is not None and m.book.is_crossed():
                self.ks.trip("crossed_book", ticker=t)
            self.ks.raise_if_tripped()
            self._quote_market(t, m, now)
            self.ks.raise_if_tripped()
        if self.live:
            self.poll_fills(now)
        for row in self.ks.update_markouts(now, lambda t: self.markets[t].book.mid_yes()
                                           if t in self.markets and self.markets[t].book else None):
            self.journal.write("markout", **row)
        eq = self.equity()
        self.ks.check_pnl(eq, datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d"))
        if now - self.last_reconcile >= cfg.loop.reconcile_every_s:
            self.reconcile(now)
        tml = self.total_max_loss()
        if tml > cfg.caps.max_total_at_risk_usd + 1e-6:
            self.ks.trip("cap_breach", total_max_loss=round(tml, 2))
        self.ks.raise_if_tripped()
        return any(not m.pulled for m in self.markets.values())

    def _quote_market(self, t: str, m: MarketState, now: float) -> None:
        cfg = self.cfg
        book = m.book
        if book is None:
            return
        for outcome in ("yes", "no"):
            other = "no" if outcome == "yes" else "yes"
            st, ot = self.om.side(t, outcome), self.om.side(t, other)
            o, oo = st.order, ot.order
            room = cfg.caps.max_total_at_risk_usd - self.total_max_loss(exclude=t)
            q = desired_quote(book, outcome, m.prog, cfg.posture, self.exposure(t),
                              o.px if o else None, (o.size - o.filled) if o else 0,
                              oo.px if oo else None, (oo.size - oo.filled) if oo else 0,
                              cfg.caps.max_per_market_at_risk_usd, room, st.filled)
            self.journal.write("decision", ticker=t, side=outcome, px=q.px, size=q.size, reason=q.reason,
                               share=round(q.share, 4), reference=q.reference, ahead=q.ahead,
                               resting_px=o.px if o else None)
            if q.px is not None or q.reason == "below_reference":
                self.ks.on_queue_ahead(t, outcome, q.ahead, m.prog.target, now)
            try:
                self.om.sync(t, outcome, q.px, q.size, now, int(m.pull_ts), q.reason)
            except PostOnlyRejected as e:
                self.ks.trip("post_only_violation", ticker=t, side=outcome, error=str(e))
                return
            except ApiError as e:
                self.ks.api_error(now, f"order {t} {outcome}: {e}")
                self.journal.write("api_error", what="order", ticker=t, side=outcome, error=str(e)[:300])
            if self.total_max_loss() > cfg.caps.max_total_at_risk_usd + 1e-6:
                self.ks.trip("cap_breach", total_max_loss=round(self.total_max_loss(), 2))
                return
        self._snapshot(t, m, now)

    def _snapshot(self, t: str, m: MarketState, now: float) -> None:
        book = m.book
        oy, on = self.om.side(t, "yes").order, self.om.side(t, "no").order
        yo = book.others("yes", oy.px if oy else None, (oy.size - oy.filled) if oy else 0)
        no = book.others("no", on.px if on else None, (on.size - on.filled) if on else 0)
        snap, excluded, sy, sn = snapshot_score(
            yo, no, m.prog.target, m.prog.df,
            (oy.px, oy.size - oy.filled) if oy else None, (on.px, on.size - on.filled) if on else None,
            self.cfg.posture.tick)
        rate = 0.0 if excluded else reward_rate_usd_per_s(m.prog.period_reward_usd, m.prog.period_s, snap)
        if m.last_snapshot_ts is not None:
            dt = min(max(0.0, now - m.last_snapshot_ts), self.cfg.kill.stale_book_s)
            m.est_reward_usd += rate * dt
        m.last_snapshot_ts = now
        m.snapshots += 1
        m.excluded_snapshots += int(excluded)
        self.journal.write("snapshot", ticker=t, program_id=m.prog.program_id, excluded=excluded,
                           share_yes=round(sy.share, 5), share_no=round(sn.share, 5), ref_yes=sy.reference,
                           ref_no=sn.reference, rate_usd_s=rate, est_reward_usd=round(m.est_reward_usd, 5),
                           yes_px=oy.px if oy else None, no_px=on.px if on else None,
                           max_loss=round(self.exposure(t).max_loss(), 2))

    # ------------------------------------------------------------------ shutdown
    def shutdown(self, reason: str) -> bool:
        """Cancel everything. Live: verify with a read-only GET and retry. Returns True when clean."""
        if self.shutdown_done:
            return True
        clean = True
        try:
            self.om.cancel_all(reason)
        except Exception as e:
            clean = False
            self.journal.write("shutdown_error", stage="cancel_all", error=f"{type(e).__name__}: {e}"[:300])
        if self.live:
            for attempt in range(3):
                try:
                    left = [o for o in self.client.get_orders("resting") if self.ids.owns(o.get("client_order_id"))]
                except Exception as e:
                    self.journal.write("shutdown_error", stage="verify", error=f"{type(e).__name__}"[:100])
                    left = None
                if left == []:
                    clean = True
                    break
                clean = False
                for o in left or []:
                    try:
                        self.client.cancel_order(o.get("order_id"), o.get("ticker"))
                    except Exception as e:
                        self.journal.write("shutdown_error", stage="cancel_retry", order_id=o.get("order_id"),
                                           error=f"{type(e).__name__}"[:100])
        self.journal.write("stop", reason=reason, clean=clean,
                           est_reward_usd={t: round(m.est_reward_usd, 4) for t, m in self.markets.items()},
                           filled={f"{t}|{s}": st.filled for (t, s), st in self.om.sides.items()})
        self.shutdown_done = True
        return clean

    def run(self, max_steps: Optional[int] = None, sleep: Callable[[float], None] = None) -> int:
        """Returns the exit code: 0 done, 2 kill trigger, 3 dirty shutdown, 130 signal."""
        code, reason = 0, "done"
        deadline = self.start_ts + self.cfg.loop.max_runtime_h * 3600.0
        try:
            self.setup()
            steps = 0
            while self.stop_requested is None:
                if not self.step():
                    reason = "all_markets_pulled"
                    break
                steps += 1
                if max_steps is not None and steps >= max_steps:
                    reason = "max_steps"
                    break
                if self.clock() >= deadline:
                    reason = "max_runtime"
                    break
                if sleep is not None:
                    sleep(self.cfg.loop.book_poll_s)
                else:
                    self._stop_event.wait(self.cfg.loop.book_poll_s)
            if self.stop_requested:
                reason, code = f"signal:{self.stop_requested}", 130
        except KillTriggered as k:
            reason, code = f"kill:{k.reason}", 2
            self.journal.write("kill", reason=k.reason, detail=k.detail)
        except KeyboardInterrupt:
            reason, code = "signal:KeyboardInterrupt", 130
        except Exception as e:
            reason, code = f"exception:{type(e).__name__}", 2
            self.journal.write("exception", error=f"{type(e).__name__}: {e}"[:500])
        finally:
            clean = self.shutdown(reason)
        return code if clean else 3


def install_signal_handlers(pilot: Pilot) -> Dict[int, object]:
    old = {}
    sigs = [signal.SIGINT, signal.SIGTERM] + ([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else [])
    for s in sigs:
        try:
            old[s] = signal.signal(s, lambda signum, frame: pilot.request_stop(signal.Signals(signum).name))
        except (ValueError, OSError):  # not main thread
            pass
    return old


def restore_signal_handlers(old: Dict[int, object]) -> None:
    for s, h in old.items():
        try:
            signal.signal(s, h)
        except (ValueError, OSError):
            pass


def build_client(cfg: PilotConfig, gate: LiveGate, transport=None, signer=None) -> KalshiClient:
    a = cfg.api
    return KalshiClient(a.base_url, gate, transport=transport, signer=signer, timeout_s=a.timeout_s,
                        read_bucket=TokenBucket(a.read_rate_per_s, max(a.read_rate_per_s, 1.0)),
                        write_bucket=TokenBucket(a.write_tokens_per_s, a.write_burst_tokens))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Kalshi LIP pilot (dry run unless all three live gates hold)")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--live", action="store_true", help="gate 1 of 3 for live order entry")
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--log-dir", type=Path, default=None)
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    gate = resolve_gate(cfg, a.live)
    if a.live and not gate.armed:
        print("--live given but gates missing -> DRY RUN:", "; ".join(gate.reasons()), file=sys.stderr)
    signer = None
    if gate.armed or cfg.api.dry_run_account_reads:
        from .auth import KalshiSigner
        signer = KalshiSigner()
    client = build_client(cfg, gate, signer=signer)
    log_dir = a.log_dir or (ROOT / cfg.paths.log_dir)
    pilot_run_id = datetime.now(timezone.utc).strftime("%y%m%d%H%M") + secrets.token_hex(3)
    journal = Journal(Path(log_dir) / f"lip_pilot_{pilot_run_id}.jsonl")
    pilot = Pilot(cfg, gate, client, journal, run_id=pilot_run_id)
    print(f"LIP pilot run {pilot_run_id}: mode={'LIVE' if pilot.live else 'DRY RUN'} log={journal.path}",
          file=sys.stderr)
    old = install_signal_handlers(pilot)
    try:
        return pilot.run(max_steps=a.max_steps)
    finally:
        restore_signal_handlers(old)


if __name__ == "__main__":
    sys.exit(main())
