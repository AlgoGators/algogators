"""Run every event against every book and write results.csv and report.md."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from .catalogue import Event
from .scenario import SEVERE, calendar, daily_vol, real_levels
from .shock import SCOPES, shock_book
from .snapshot import Snapshot

SEVERITIES = {"central": 1.0, "severe": SEVERE}
BOOK_LABELS = {"system": "A system", "qt_proposal": "B proposal", "qt": "C final"}
EDIT = "Edit"
HEADLINE_SCOPE = "sector"


def merge_identical(books: Mapping[str, Mapping[str, float]]) -> dict[str, dict[str, float]]:
    """Books with the same positions collapse into one column ("A system = C final")."""
    groups: dict[tuple[tuple[str, float], ...], list[str]] = {}
    for name, book in books.items():
        groups.setdefault(tuple(sorted(book.items())), []).append(BOOK_LABELS.get(name, name))
    return {" = ".join(names): dict(key) for key, names in groups.items()}


def run_all(
    snap: Snapshot, events: Sequence[Event], books: Mapping[str, Mapping[str, float]]
) -> pd.DataFrame:
    notional = snap.notional_per_contract()
    rows = []
    for ev in events:
        sessions = len(calendar(ev, snap.closes))
        for label, book in books.items():
            for scope in SCOPES:
                for sev_name, sev in SEVERITIES.items():
                    s = shock_book(book, notional, ev, snap.closes, scope, sev, snap.sectors)
                    rows.append(
                        {
                            "event": ev.id,
                            "name": ev.name,
                            "klass": ev.klass,
                            "variant": ev.variant,
                            "start": ev.start.date(),
                            "end": ev.end.date(),
                            "sessions": sessions,
                            "book": label,
                            "scope": scope,
                            "severity": sev_name,
                            "end_usd": s.end,
                            "end_pct": 100 * s.end / snap.equity,
                            "worst_usd": s.worst,
                            "worst_pct": 100 * s.worst / snap.equity,
                            "worst_date": s.worst_date.date() if s.worst_date is not None else None,
                            "contributors": " ".join(
                                f"{r}:{100 * v / snap.equity:+.2f}%" for r, v in s.contributors()
                            ),
                            "uncovered": " ".join(s.uncovered),
                            "k": " ".join(f"{r}:{m.k:.2f}" for r, m in s.moves.items()),
                            "floored": " ".join(r for r, m in s.moves.items() if m.floored),
                        }
                    )
    return pd.DataFrame(rows)


def _fmt_book(book: Mapping[str, float]) -> str:
    return ", ".join(f"{r} {q:+g}" for r, q in sorted(book.items())) or "(flat)"


def _pct(v: float) -> str:
    return f"{v:+.2f}%" if abs(v) >= 0.005 else "0.00%"


def _exposure_rows(snap: Snapshot, book: Mapping[str, float]) -> list[str]:
    notional = snap.notional_per_contract()
    out = []
    for r, q in sorted(book.items(), key=lambda kv: -abs(kv[1] * notional[kv[0]])):
        n = q * notional[r]
        vol = daily_vol(snap.closes[r])
        out.append(
            f"| {r} | {snap.sectors.get(r, '?')} | {q:+g} | ${n:,.0f} | "
            f"{100 * n / snap.equity:+.1f}% | {100 * vol * np.sqrt(252):.1f}% | "
            f"{100 * abs(n) * vol / snap.equity:.2f}% |"
        )
    return out


def write_report(
    snap: Snapshot,
    books: Mapping[str, Mapping[str, float]],
    results: pd.DataFrame,
    out: Path,
    base: str,
    edit_spec: str | None = None,
) -> Path:
    """`base` is the book the edit starts from and the one the detail columns describe."""
    out.mkdir(parents=True, exist_ok=True)
    results.to_csv(out / "results.csv", index=False)

    def pick(book: str, scope: str = HEADLINE_SCOPE, severity: str = "central") -> pd.DataFrame:
        sel = results[
            (results.book == book) & (results.scope == scope) & (results.severity == severity)
        ]
        return sel.set_index("event")

    tables = {b: pick(b) for b in books}
    head_base = tables[base]
    named, all_mk, severe = pick(base, "named"), pick(base, "all"), pick(base, severity="severe")
    notional = snap.notional_per_contract()
    # The engine's live_results row describes the system book; check our pricing against it.
    system = next((b for b in books if BOOK_LABELS["system"] in b.split(" = ")), base)
    gross = sum(abs(q) * notional[r] for r, q in books[system].items())
    engine_gross = snap.engine.get("gross_notional")
    has_edit = EDIT in books
    shown = [base, *(b for b in books if b not in (base, EDIT))] + ([EDIT] if has_edit else [])

    lines = [
        f"# Stress test: {snap.portfolio_id} on {snap.day} (Tier 1, book held unchanged)",
        "",
        f"Equity ${snap.equity:,.2f} "
        + (
            f"(the account at the close of {snap.account_date}). "
            if snap.account_date
            else f"(engine, {snap.engine.get('date')}). "
        )
        + f"Prices to {snap.closes.index[-1].date()}. Gross notional of {system}: ${gross:,.2f}"
        + (f" (engine: ${float(engine_gross):,.2f})." if engine_gross else "."),
        "",
        "| Book | Positions (contracts) |",
        "|---|---|",
        *(
            f"| {b} | {_fmt_book(books[b])}"
            + (f" ({base} with {edit_spec})" if b == EDIT and edit_spec else "")
            + " |"
            for b in shown
        ),
        "",
        f"## Exposure of {base}",
        "",
        "Today's volatility drives the rescale: an instrument twice as volatile as before an "
        "event gets that event's move doubled (capped at 2x).",
        "",
        "| Instrument | Sector | Contracts | Notional | % of equity | Vol now (annual) | 1-day 1 sd, % of equity |",
        "|---|---|---|---|---|---|---|",
        *_exposure_rows(snap, books[base]),
        "",
        "## Worst point during each event, % of equity",
        "",
        "Book columns move the event's named instruments plus held instruments in the same "
        "sector, at the event's real severity rescaled to today's volatility. The last four "
        f"columns describe {base}: named instruments only, every held instrument as it really "
        "moved, the generator's 95th-percentile severity, and the largest contributions at "
        "the worst session (at the end of the window if it never lost).",
        "",
    ]
    head = ["Event", "Sessions", *shown]
    if has_edit:
        head.append("Edit change")
    head += ["Named only", "All markets", "Severe", "Biggest moves"]
    lines += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for eid in head_base.sort_values("worst_pct").index:
        r = head_base.loc[eid]
        variant = str(r["variant"]) if pd.notna(r["variant"]) else ""
        title = f"{eid} {str(r['name'])[:44]}" + (f" [{variant[:40]}]" if variant else "")
        cells = [title, str(r["sessions"])]
        cells += [_pct(tables[b].loc[eid, "worst_pct"]) for b in shown]
        if has_edit:
            cells.append(_pct(tables[EDIT].loc[eid, "worst_pct"] - r["worst_pct"]))
        unc = all_mk.loc[eid, "uncovered"]
        cells += [
            _pct(named.loc[eid, "worst_pct"]),
            _pct(all_mk.loc[eid, "worst_pct"]) + (f" (no data: {unc})" if unc else ""),
            _pct(severe.loc[eid, "worst_pct"]),
            str(r["contributors"]) or "none held",
        ]
        lines.append("| " + " | ".join(cells) + " |")

    worst = {b: tables[b]["worst_pct"] for b in shown}
    lines += [
        "",
        "| Summary | " + " | ".join(shown) + " |",
        "|---|" + "---|" * len(shown),
        "| Worst event | "
        + " | ".join(f"{_pct(w.min())} ({w.idxmin()})" for w in worst.values())
        + " |",
        "| Median over events | "
        + " | ".join(_pct(float(np.median(w))) for w in worst.values())
        + " |",
        "| Events losing over 2% | "
        + " | ".join(str(int((w < -2).sum())) for w in worst.values())
        + " |",
        "",
        "## How to read this",
        "",
        "- The book is held unchanged through the event: no trading, no stops, no resizing. "
        "For single-session events that is close to reality. For multi-week events it is a "
        "bound, not a forecast; the dynamic replay (Tier 2) answers that.",
        "- Moves are recomputed from our own daily closes and rescaled per instrument to "
        "today's volatility (k in results.csv), with the generator's realism floor (80% of "
        "the all-time low). Because the floor caps a big loser while gains elsewhere keep "
        "scaling, 'Severe' can come out milder than central on a mixed book.",
        "- Prices only: no margin calls, intraday gaps, limit locks or liquidity costs, so a "
        "loss here understates the real one.",
        "- Our continuous series are the raw front month, not back-adjusted: over a window "
        "that spans a roll, the move includes the roll gap, which a held position would not "
        "earn. The catalogue measured its moves the same way.",
        "- 'No data' means our history does not cover that instrument at the event (MBT "
        "starts in 2021); it contributes nothing. Over long windows 'All markets' includes "
        "moves that had nothing to do with the event. No catalogued event names a crypto "
        "contract, so MBT moves only under 'All markets'.",
        "- results.csv has every scope and severity, the end-of-window P&L, the worst "
        "session's date and the volatility rescale per instrument.",
    ]
    path = out / "report.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def selfcheck(events: Sequence[Event], closes: pd.DataFrame, tol: float = 1e-3) -> pd.DataFrame:
    """Recompute the package's measured numbers from our prices with this package's own code.

    Pass closes loaded with weekend bars kept, as the package measured them.
    """
    rows = []
    for ev in events:
        cal = calendar(ev, closes)
        for r, m in ev.measured.iterrows():
            lv = real_levels(closes[r], ev, cal) if r in closes.columns else None
            if lv is None:
                rows.append({"event": ev.id, "root": r, "metric": "missing", "diff": np.nan})
                continue
            if ev.single:
                ours = {"move": float(lv.iloc[-1])}
            else:
                c = 1 + lv.to_numpy()
                c = c / c[0]
                ours = {
                    "net": float(c[-1] - 1),
                    "mdd": float(np.min(c / np.maximum.accumulate(c) - 1)),
                }
            for metric, v in ours.items():
                rows.append(
                    {
                        "event": ev.id,
                        "root": r,
                        "metric": metric,
                        "package": float(m[metric]),
                        "ours": v,
                        "diff": v - float(m[metric]),
                    }
                )
    df = pd.DataFrame(rows)
    df["ok"] = df["diff"].abs() <= tol
    return df
