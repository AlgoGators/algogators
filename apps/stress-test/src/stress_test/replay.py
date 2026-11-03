"""Tier 2: the strategy trades through each event, through the published engine, unchanged.

The engine (`tradengin` as published) starts every backtest flat: it has no way to start from a
given book. So each run starts flat `lead_in` sessions before the day, trades the strategy
production recorded for the day (its settings, warmed up on the stored history, sized on the day's
sizing capital) through that stretch of real history, arrives at the day holding the strategy's
own book, and trades on through the event bars. That book is the strategy's, not the stored one:
production's book carries its whole history through the no-trade buffer, and any desk change. A
tested book's difference from it is carried alongside and priced on the same path, Tier 1's
arithmetic; by default each symbol's difference lasts until the strategy itself trades that symbol
(`difference_path`):

    tested book = the strategy's own book (the engine, trading) + the difference (carried)

Every event run is paired with a no-event run of the same length (no instrument moves), so what the
strategy would have done anyway is separated from what the event did. Runs depend on the event and
the severity only, not on the tested book.

`plan_check` measures how far the strategy's own book is from production's: for each stored day, a
run that starts as a replay does, its book on the day against the stored system book.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from . import engine
from .catalogue import Event
from .paths import EventPath, event_path, history_for_replay, roll_adjusted
from .report import SEVERITIES, merge_identical
from .shock import apply_edits, shock_book
from .snapshot import BOOKS, Snapshot, load_bars, load_positions_history, root

HEADLINE_SCOPE = "sector"
DEFAULT_LEAD_IN = 20  # sessions of real history the strategy trades before the day


# ----------------------------------------------------------------------------- one session
@dataclass
class Session:
    """What every run of one replay shares: the snapshot's history, config tree and engine."""

    snap: Snapshot
    workdir: Path
    module_dir: str | None = None
    lead_in: int = DEFAULT_LEAD_IN
    adjust_rolls: bool = True  # the engine reads no contract ids: take the roll gaps out first
    history: pd.DataFrame = field(init=False)
    history_file: str = field(init=False)
    config_dir: str = field(init=False)
    warmup: int = field(init=False)
    bar_dates: list[dt.date] = field(init=False)

    def __post_init__(self) -> None:
        if self.snap.folder is None:
            raise SystemExit("the snapshot was not loaded from a folder")
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.history = history_for_replay(load_bars(self.snap.folder), self.snap)
        if self.adjust_rolls:
            self.history = roll_adjusted(self.history)
        self.history_file = engine.write_bars(self.history, self.workdir / "history.csv")
        # Sized as production sizes the day's book: half compounding's capital, the account less
        # its drawdown from the running peak (sizing_capital.hpp; the account itself below a
        # starting capital never exceeded).
        self.config_dir = str(
            engine.write_config_tree(
                self.snap.settings, self.workdir / "config", self.sizing_capital
            )
        )
        self.warmup = engine.warmup_sessions(self.snap.settings)
        self.bar_dates = sorted(self.history["date"].dt.date.unique())

    @property
    def sizing_capital(self) -> float:
        return self.snap.equity - self.snap.peak

    def start_for(self, first_session: dt.date) -> dt.date:
        """The window's first bar date: the warm-up and the lead-in before `first_session`."""
        before = [d for d in self.bar_dates if d < first_session]
        k = max(0, len(before) - self.lead_in - self.warmup)
        return before[k] if before else first_session

    def spec(
        self, name: str, bars: list[str], first_session: dt.date, end: dt.date
    ) -> engine.RunSpec:
        last_real = max(d for d in self.bar_dates if d < first_session)
        return engine.RunSpec(
            bars=bars,
            contracts=str(self.snap.folder / "contracts.csv"),  # type: ignore[operator]
            config_dir=self.config_dir,
            out_dir=str(self.workdir / "runs" / name),
            start=self.start_for(first_session).isoformat(),
            end=end.isoformat(),
            keep_from=last_real.isoformat(),
            module_dir=self.module_dir,
        )


def run_all(
    specs: Mapping[str, engine.RunSpec],
    jobs: int,
    runner: Callable[[engine.RunSpec], engine.RunResult] = engine.run_in_subprocess,
    progress: Callable[[str, engine.RunResult], None] | None = None,
) -> dict[str, engine.RunResult]:
    """Each spec in its own process, `jobs` at a time (the threads only wait on them)."""
    out: dict[str, engine.RunResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(runner, s): name for name, s in specs.items()}
        for fut, name in futures.items():
            out[name] = fut.result()
            if progress:
                progress(name, out[name])
    return out


# ----------------------------------------------------------------------------- event replays
@dataclass(frozen=True)
class PlannedPath:
    event: Event | None  # None: the no-event twin
    severity: str
    path: EventPath
    file: str


def plan_replays(
    session: Session,
    events: Sequence[Event],
    scope: str = HEADLINE_SCOPE,
    severities: Mapping[str, float] = SEVERITIES,
) -> tuple[dict[str, engine.RunSpec], dict[str, PlannedPath], list[str]]:
    """One run per event x severity, and one no-event run per distinct event length."""
    snap = session.snap
    specs: dict[str, engine.RunSpec] = {}
    paths: dict[str, PlannedPath] = {}
    skipped: list[str] = []
    pdir = session.workdir / "paths"
    pdir.mkdir(parents=True, exist_ok=True)
    lengths: set[int] = set()
    for ev in events:
        for sev_name, sev in severities.items():
            try:
                p = event_path(snap, session.history, ev, scope, sev)
            except ValueError:
                skipped.append(ev.id)
                break
            key = f"{ev.id}~{sev_name}"
            paths[key] = PlannedPath(
                ev, sev_name, p, engine.write_bars(p.bars, pdir / f"{key}.csv")
            )
            lengths.add(len(p.dates))
    for n in sorted(lengths):
        p = event_path(snap, session.history, None, sessions=n)
        key = f"none~{n}"
        paths[key] = PlannedPath(None, "none", p, engine.write_bars(p.bars, pdir / f"{key}.csv"))
    for key, pp in paths.items():
        d = pp.path.dates
        specs[key] = session.spec(key, [session.history_file, pp.file], d[0].date(), d[-1].date())
    return specs, paths, skipped


@dataclass(frozen=True)
class Outcome:
    """One engine run over an event path: the strategy's own book."""

    pnl: pd.Series  # account change from the close before the event, per session (dollars)
    books: pd.DataFrame  # index: session; columns: root; contracts
    start_book: dict[str, float]  # the book the first event session is marked on
    traded: float  # contracts bought and sold from the close before the event to the end


def outcome(result: engine.RunResult, dates: pd.DatetimeIndex) -> Outcome:
    eq = pd.Series({pd.Timestamp(d): v for d, v in result.equity}).sort_index()
    eq = eq[~eq.index.duplicated(keep="last")]
    before = eq[eq.index < dates[0]]
    base = float(before.iloc[-1]) if len(before) else float(eq.iloc[0])
    pos = pd.DataFrame(result.positions, columns=["date", "symbol", "quantity"])
    pos["date"] = pd.to_datetime(pos["date"])
    pos["root"] = pos["symbol"].map(lambda s: root(str(s)))
    allbooks = pos.pivot_table(index="date", columns="root", values="quantity", aggfunc="sum")
    prior = allbooks[allbooks.index < dates[0]]
    books = allbooks.reindex(dates).ffill().fillna(0.0)
    held_before = prior.iloc[-1].fillna(0.0) if len(prior) else books.iloc[0] * 0
    traded = float(
        pd.concat([held_before.to_frame().T, books]).fillna(0.0).diff().abs().sum().sum()
    )
    first = books.iloc[0]
    return Outcome(
        pnl=eq.reindex(dates).ffill() - base,
        books=books,
        start_book={str(r): float(q) for r, q in first.items() if abs(q) > 1e-9},
        traded=traded,
    )


def held_difference(book: Mapping[str, float], start_book: Mapping[str, float]) -> dict[str, float]:
    """What the tested book holds beyond (or short of) the strategy's own book at the start."""
    roots = set(book) | set(start_book)
    diff = {r: float(book.get(r, 0.0)) - float(start_book.get(r, 0.0)) for r in roots}
    return {r: q for r, q in diff.items() if abs(q) > 1e-9}


DIFFERENCE_MODES = ("until-traded", "held")


def difference_path(
    diff: Mapping[str, float], books: pd.DataFrame, mode: str = "until-traded"
) -> pd.DataFrame:
    """The difference per session (index: the event's sessions; columns: root).

    until-traded (the default): a symbol's difference stays until the strategy itself trades
    that symbol, and from that session the tested book follows the strategy's. Production's book
    stays put where its buffer holds it while the strategy leaves the symbol alone; once the
    strategy acts on the symbol, production would have acted too.
    held: the difference stays through the whole event (a desk change kept in place).
    """
    if mode not in DIFFERENCE_MODES:
        raise ValueError(f"mode must be one of {DIFFERENCE_MODES}")
    out = {}
    for r, q in diff.items():
        level = pd.Series(q, index=books.index, dtype=float)
        if mode == "until-traded":
            own = books[r] if r in books.columns else pd.Series(0.0, index=books.index)
            acted = own.diff().fillna(0.0).ne(0.0).cumsum().gt(0)
            level = level.where(~acted, 0.0)
        out[r] = level
    return pd.DataFrame(out, index=books.index)


def difference_pnl(
    held: pd.DataFrame, path: EventPath, multipliers: Mapping[str, float]
) -> tuple[pd.Series, list[str]]:
    """The difference priced on the path the engine traded: the contracts held over each session
    times that session's move (base x contract size x the change in level), cumulated."""
    total = pd.Series(0.0, index=path.dates)
    unpriced = []
    for r in held.columns:
        if r not in path.levels.columns or r not in multipliers:
            unpriced.append(r)
            continue
        step = path.levels[r].diff().fillna(path.levels[r].iloc[0])
        total = total + held[r] * path.base[r] * multipliers[r] * step
    return total.cumsum(), unpriced


def replay_books(
    snap: Snapshot, edit: Mapping[str, float] | None, edit_base: str
) -> dict[str, dict[str, float]]:
    """The distinct stored books (identical ones merged, as in Tier 1) plus the edited one."""
    books = merge_identical({b: snap.books.get(b, {}) for b in BOOKS})
    if edit:
        if edit_base not in snap.books:
            raise SystemExit(f"no {edit_base} book on {snap.day}")
        books["Edit"] = apply_edits(snap.books[edit_base], edit)
    return books


def summarise(
    session: Session,
    paths: Mapping[str, PlannedPath],
    results: Mapping[str, engine.RunResult],
    books: Mapping[str, Mapping[str, float]],
    scope: str = HEADLINE_SCOPE,
    mode: str = "until-traded",
) -> pd.DataFrame:
    snap = session.snap
    notional = snap.notional_per_contract()
    rows: list[dict[str, Any]] = []
    for key, pp in paths.items():
        if pp.event is None:
            continue
        res = results[key]
        n = len(pp.path.dates)
        twin_key = f"none~{n}"
        twin = results.get(twin_key)
        o = outcome(res, pp.path.dates) if res.ok else None
        t = outcome(twin, paths[twin_key].path.dates) if twin is not None and twin.ok else None
        for label, book in books.items():
            row: dict[str, Any] = {
                "event": pp.event.id,
                "name": pp.event.name,
                "book": label,
                "severity": pp.severity,
                "scope": scope,
                "sessions": n,
                "ok": res.ok,
                "error": res.error,
            }
            static = shock_book(
                book, notional, pp.event, snap.closes, scope, SEVERITIES[pp.severity], snap.sectors
            )
            row["held_worst_pct"] = 100 * static.worst / snap.equity
            if o is not None:
                diff = held_difference(book, o.start_book)
                held = difference_path(diff, o.books, mode)
                dpnl, unpriced = difference_pnl(held, pp.path, snap.multipliers)
                total = o.pnl + dpnl
                worst = float(min(0.0, total.min()))
                end_book = {
                    r: q
                    for r, q in (
                        o.books.iloc[-1].add(
                            held.iloc[-1] if len(held.columns) else 0.0, fill_value=0.0
                        )
                    ).items()
                    if abs(q) > 1e-9
                }
                row.update(
                    {
                        "traded_worst_pct": 100 * worst / snap.equity,
                        "traded_worst_date": total.idxmin().date() if worst < 0 else None,
                        "traded_end_pct": 100 * float(total.iloc[-1]) / snap.equity,
                        "strategy_end_pct": 100 * float(o.pnl.iloc[-1]) / snap.equity,
                        "difference_end_pct": 100 * float(dpnl.iloc[-1]) / snap.equity,
                        "strategy_start_book": _fmt(o.start_book),
                        "held_difference": _fmt(diff),
                        "unpriced": " ".join(unpriced),
                        "contracts_traded": o.traded,
                        "end_book": _fmt(end_book),
                    }
                )
                if t is not None:
                    tdiff = held_difference(book, t.start_book)
                    tpnl, _ = difference_pnl(
                        difference_path(tdiff, t.books, mode),
                        paths[twin_key].path,
                        snap.multipliers,
                    )
                    row["no_event_end_pct"] = 100 * float((t.pnl + tpnl).iloc[-1]) / snap.equity
                    row["event_effect_end_pct"] = row["traded_end_pct"] - row["no_event_end_pct"]
            rows.append(row)
    return pd.DataFrame(rows)


def _fmt(book: Mapping[str, float]) -> str:
    return ", ".join(f"{r} {q:+g}" for r, q in sorted(book.items()) if abs(q) > 1e-9) or "(flat)"


# ----------------------------------------------------------------------------- fidelity check
def plan_check(
    session: Session, book: str = "system", last: int | None = None
) -> tuple[dict[str, engine.RunSpec], pd.DataFrame]:
    """One run per stored day the snapshot has bars for, as a replay starts: flat, the lead-in
    before that day, so the strategy's own book on the day can be set against the stored `book`."""
    stored = load_positions_history(session.snap.folder)  # type: ignore[arg-type]
    stored = stored[stored["book"] == book]
    have = set(session.bar_dates)
    days = [d for d in sorted(stored["date"].dt.date.unique()) if d in have]
    if last:
        days = days[-last:]
    if not days:
        raise SystemExit(f"the snapshot has no stored {book} day with bars to check")
    specs = {}
    for day in days:
        spec = session.spec(f"check~{day.isoformat()}", [session.history_file], day, day)
        spec.keep_from = day.isoformat()
        specs[day.isoformat()] = spec
    return specs, stored[stored["date"].dt.date.isin(days)]


def check_table(results: Mapping[str, engine.RunResult], stored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day, g in stored.groupby(stored["date"].dt.date):
        key = day.isoformat()
        res = results.get(key)
        if res is None:
            continue
        row: dict[str, Any] = {"day": key, "ok": res.ok, "error": res.error}
        if res.ok:
            want = {root(str(s)): float(q) for s, q in zip(g["symbol"], g["quantity"], strict=True)}
            got: dict[str, float] = {}
            for d, s, q in res.positions:
                if d == key and abs(q) > 1e-9:
                    got[root(s)] = got.get(root(s), 0.0) + q
            diff = {
                r: (want.get(r, 0.0), got.get(r, 0.0))
                for r in sorted(set(want) | set(got))
                if abs(want.get(r, 0.0) - got.get(r, 0.0)) > 1e-9
            }
            row.update(
                {
                    "match": not diff,
                    "contracts_apart": sum(abs(a - b) for a, b in diff.values()),
                    "differences": " ".join(f"{r}:{a:+g}/{b:+g}" for r, (a, b) in diff.items()),
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- report
def _pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n/a"
    return f"{v:+.2f}%" if abs(v) >= 0.005 else "0.00%"


def write_replay_report(
    snap: Snapshot,
    caps: engine.Capabilities,
    table: pd.DataFrame,
    books: Mapping[str, Mapping[str, float]],
    out: Path,
    lead_in: int,
    skipped: Sequence[str] = (),
    mode: str = "until-traded",
) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "replay.csv", index=False)
    ok = table[table["ok"]]
    start = ok["strategy_start_book"].iloc[0] if "strategy_start_book" in ok else "n/a"
    lines = [
        f"# Stress test: {snap.portfolio_id} on {snap.day} (Tier 2, the strategy trades through "
        "each event)",
        "",
        f"Engine: tradengin {caps.version or '(version unknown)'} at `{caps.location}`, "
        "unchanged. "
        f"Account ${snap.equity:,.2f}"
        + (f" (close of {snap.account_date})" if snap.account_date else "")
        + f"; the strategy trades from flat over the {lead_in} sessions before the day.",
        "",
        *(f"- Caveat: {c}." for c in caps.caveats()),
        "",
        f"The strategy's own book on the day (the engine's): {start}.",
        "",
        "| Tested book | Positions (contracts) | Difference from the strategy's book |",
        "|---|---|---|",
    ]
    for label, book in books.items():
        diff = ok[ok["book"] == label].get("held_difference", pd.Series(dtype=str))
        lines.append(f"| {label} | {_fmt(book)} | {diff.iloc[0] if len(diff) else 'n/a'} |")
    lines.append("")
    for label in books:
        t = ok[ok["book"] == label]
        if t.empty:
            continue
        central = t[t["severity"] == "central"].set_index("event")
        severe = t[t["severity"] == "severe"].set_index("event")
        lines += [
            f"## {label}: worst point and end of each event, % of the account",
            "",
            "Held: Tier 1, the book unchanged. Traded: the strategy's own book rebalancing daily "
            "from the event's first session, plus the tested book's difference from it "
            + (
                "(each symbol's until the strategy trades that symbol)"
                if mode == "until-traded"
                else "(held through the event)"
            )
            + ". No event: the same, with no instrument moving. Event effect: traded end less "
            "no-event end.",
            "",
            "| Event | Sessions | Held worst | Traded worst | Traded end | of which the "
            "difference | No event end | Event effect | Severe traded worst | Strategy's "
            "contracts traded | Book at the end |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for eid in central.sort_values("traded_worst_pct").index:
            r = central.loc[eid]
            sv = severe["traded_worst_pct"].get(eid) if len(severe) else None
            lines.append(
                f"| {eid} {str(r['name'])[:44]} | {r['sessions']} | "
                f"{_pct(r['held_worst_pct'])} | {_pct(r['traded_worst_pct'])} | "
                f"{_pct(r['traded_end_pct'])} | {_pct(r['difference_end_pct'])} | "
                f"{_pct(r.get('no_event_end_pct'))} | {_pct(r.get('event_effect_end_pct'))} | "
                f"{_pct(sv)} | {r['contracts_traded']:.0f} | {r['end_book']} |"
            )
        w, s = central["traded_worst_pct"], central["held_worst_pct"]
        lines += [
            "",
            "| Summary | Held | Traded |",
            "|---|---|---|",
            f"| Worst event | {_pct(s.min())} ({s.idxmin()}) | {_pct(w.min())} ({w.idxmin()}) |",
            f"| Median over events | {_pct(s.median())} | {_pct(w.median())} |",
            f"| Events losing over 2% | {int((s < -2).sum())} | {int((w < -2).sum())} |",
            "",
        ]
    failed = table[~table["ok"]].drop_duplicates(["event", "severity"])
    if len(failed):
        lines += ["## Runs that failed", ""]
        lines += [f"- {r.event} {r.severity}: {r.error}" for r in failed.itertuples()]
        lines.append("")
    if skipped:
        lines += [f"Skipped (no named instrument in our history): {', '.join(skipped)}.", ""]
    lines += [
        "## How to read this",
        "",
        "- The engine is used as published: it cannot start from a given book. The strategy "
        "production recorded for the day (its settings, warmed up on the stored history, sized on "
        "the day's half-compounded capital) trades from flat through the last sessions before the "
        "day and arrives with "
        "its own book; `replay-check` shows how far that is from the stored book. "
        + (
            "The tested book's difference from it stays, symbol by symbol, until the strategy "
            "itself trades that symbol; from then the tested book follows the strategy (its "
            "buffer keeps a position in place until the strategy acts on the symbol). "
            "`--difference held` keeps the difference through the whole event instead."
            if mode == "until-traded"
            else "The tested book's difference from it is held through the whole event (a desk "
            "change or an edit kept in place) while the strategy trades its own book."
        ),
        "- An engine that reads no contract ids (the published module) gets the history with "
        "each roll's price gap taken out (the roll bar's return set to 0, earlier prices scaled), "
        "which is how production books a roll; otherwise it would read the gaps as moves.",
        "- From the event's first session the strategy rebalances once a day on the previous "
        "close, with its buffer, risk overlay and costs, as production does.",
        "- The prices are Tier 1's: each instrument's real move in the event, rescaled to "
        "today's volatility and floored, applied to its last close. Instruments the scope does "
        "not name alternate half a daily standard deviation around their last close (a flat "
        "price would make the engine size them up). Session i is the i-th weekday from today; an "
        "exchange holiday among them is held by the engine and its move lands on the next "
        "session.",
        "- One path per event. Prices only: no margin calls, gaps inside a session, limit locks "
        "or liquidity beyond the cost model.",
    ]
    path = out / "replay.md"
    path.write_text("\n".join(lines) + "\n")
    return path
