"""
Scaffold a new research study so it plugs into the ledger and the strategy catalog.

Creates (never overwrites):
  scripts/research/ledger_entries/<prefix>.yaml   one `unproven` stub entry
  docs/<PREFIX>_<SHORT_TITLE>.md                  report from docs/strategies/TEMPLATE.md (topic name, no date;
                                                  `Last revised: <date>` under the title)
  scripts/research/<prefix>_study.py              script stub wired to the shared building blocks

    uv run python scripts/research/new_strategy.py <prefix> "<title>" [--venue kalshi] [--date YYYY-MM-DD] [--dry-run]

Then: write the pre-registration in the doc, implement the study, log every variant in research/trials.csv,
fill in the ledger YAML verdict, and run
    uv run python scripts/research/build_strategy_catalog.py
See docs/strategies/README.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "docs" / "strategies" / "TEMPLATE.md"

SCRIPT_STUB = '''"""
{prefix}: {title}. Research only: no orders, no keys.

Report: {doc}
Ledger: scripts/research/ledger_entries/{prefix}.yaml
Data:   data/vendor/{prefix}/

usage: uv run python scripts/research/{prefix}_study.py
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts/research"))

# Core building blocks (predict_ngin; see docs/strategies/README.md). Drop what the study does not use.
from predict_ngin import lake  # lake.connect(): DuckDB with standard limits + views (kalshi_trades, polymarket_maker_fills, ...)
from predict_ngin.tapes.kalshi import tape_sql  # SQL over the full Kalshi tape; pass start_ts/end_ts to read only those months
from predict_ngin.tapes.polymarket import clean_trades_sql  # SQL over every Polymarket fill (maker_rows_only=True for volume)
from predict_ngin.costs import kalshi_order_fee, maker_fee_applies  # Kalshi fee per order, rounded up to the cent
from predict_ngin.costs.kalshi_schedule import load_schedule, pit  # point-in-time series fee schedule
from predict_ngin.evaluation.metrics import cluster_bootstrap_ci, deflated_sharpe, sharpe  # pass periods_per_year
from predict_ngin.markets import read_settled_markets, series_of, settled_frame  # tickers and settlements
from predict_ngin.venues.kalshi import KalshiReadClient  # GET-only Kalshi API client (public reads need no key)

# Older helpers, not yet in the core; import only what the study needs.
# from src.data.tapes import open_tape                      # mmapped Kalshi tape: open_tape("kalshi_base")
# from src.data.fills import causal_fills, MODELS           # causal Fa/Fb/Fc fills (numba)
# from src.data.runner import run_strategies, ram_sized_workers  # many strategies, one pass over the tape

OUT = ROOT / "data" / "vendor" / "{prefix}"
TRIALS = ROOT / "research" / "trials.csv"
TRIAL_COLS = ["timestamp", "strategy", "variant", "hypothesis", "params", "data_range", "sample", "sharpe",
              "ann_return", "max_dd", "turnover", "n_obs", "status", "git_commit", "notes"]


def log_trial(variant: str, hypothesis: str, params: dict, data_range: str, sample: str, status: str,
              notes: str = "", **metrics) -> None:
    """Append one row to research/trials.csv (append-only; log every variant, including failures)."""
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                               text=True, timeout=10).stdout.strip()
        commit += "-dirty" if dirty else ""
    except Exception:
        commit = ""
    row = dict(timestamp=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), strategy="{prefix}",
               variant=variant, hypothesis=hypothesis, params=json.dumps(params), data_range=data_range,
               sample=sample, status=status, git_commit=commit, notes=notes)
    row.update({{k: v for k, v in metrics.items() if k in TRIAL_COLS}})
    with TRIALS.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=TRIAL_COLS).writerow({{c: row.get(c, "") for c in TRIAL_COLS}})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raise SystemExit("TODO: implement the pre-registered study in {doc}")


if __name__ == "__main__":
    main()
'''

YAML_STUB = """# {prefix} ({date}): {title}.
# Source: {doc} (scripts/research/{prefix}_*.py, data/vendor/{prefix}/)
# Keys = LedgerEntry fields (src/backtest_v3/results_db.py). status: rejected | invalid | capacity_capped |
# unproven | candidate | live. Seed with `uv run python scripts/research/seed_ledger.py` (writes results.db),
# then regenerate the catalog with `uv run python scripts/research/build_strategy_catalog.py`.
- strategy_id: {prefix}_main
  name: "{title}"
  venue: {venue}
  status: unproven
  evidence: {doc}
  verdict: "Scaffolded {date}; not yet tested."
  key_numbers: {{}}
  gaps: ""
  next_tests: "Write the pre-registration in {doc}, then run scripts/research/{prefix}_study.py."
  retest_if: ""
"""


def slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").upper()
    return "_".join(s.split("_")[:5]) or "STUDY"


def plan(prefix: str, title: str, venue: str, date: str, root: Path = ROOT) -> dict[Path, str]:
    if not re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)?", prefix):
        raise ValueError("prefix must be short lower-case, e.g. 'perps' or 'fin_f' (letters/digits, one optional '_')")
    taken = {p.stem.split("_")[0] for p in (root / "scripts" / "research").glob("*.py")}
    taken |= {p.stem for p in (root / "scripts" / "research" / "ledger_entries").glob("*.yaml")}
    if prefix in taken or (root / "scripts" / "research" / "ledger_entries" / f"{prefix}.yaml").exists():
        raise ValueError(f"prefix '{prefix}' is already used by existing scripts or a ledger file; pick another")
    doc_rel = f"docs/{prefix.upper()}_{slug(title)}.md"  # topic name; the date goes in `Last revised:`
    template = (root / "docs" / "strategies" / "TEMPLATE.md").read_text(encoding="utf-8")
    doc = re.sub(r"<!--.*?-->\n*", "", template, flags=re.S)
    for k, v in {"{PREFIX}": prefix.upper(), "{prefix}": prefix, "{one-line title}": title, "{YYYY-MM-DD}": date,
                 "{SHORT_TITLE}": slug(title)}.items():
        doc = doc.replace(k, v)
    fmt = dict(prefix=prefix, title=title.replace('"', "'"), venue=venue, date=date, doc=doc_rel)
    return {
        root / "scripts" / "research" / "ledger_entries" / f"{prefix}.yaml": YAML_STUB.format(**fmt),
        root / doc_rel: doc,
        root / "scripts" / "research" / f"{prefix}_study.py": SCRIPT_STUB.format(**fmt),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prefix")
    ap.add_argument("title")
    ap.add_argument("--venue", default="kalshi")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        files = plan(a.prefix, a.title, a.venue, a.date)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for path, content in files.items():
        if path.exists():
            print(f"error: {path.relative_to(ROOT)} exists; not overwriting", file=sys.stderr)
            return 2
    for path, content in files.items():
        print(("would create " if a.dry_run else "created ") + path.relative_to(ROOT).as_posix())
        if not a.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if not a.dry_run:
        print("next: write the prereg in the doc, implement, log trials, set the verdict, then run "
              "uv run python scripts/research/build_strategy_catalog.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
