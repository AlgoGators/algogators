"""File readers behind the Paper trading page (no Streamlit, so they are unit-tested).

Three sources, all optional:

* **Paper runners.** Every ``<data>/paper_trading/<name>/`` folder with a ``status.json`` (the
  :mod:`predict_ngin.paper.runner` format: ``name``, ``mode``, ``ok``, ``last_run`` and any extra
  fields), plus folders listed in the machine-local ``config/paper_monitor.local.yaml``
  (gitignored; see ``config/paper_monitor.example.yaml``). A listed folder needs no
  ``status.json``: its newest file then stands in for the last run.
* **Order journal.** ``<data>/live/journal/YYYY-MM-DD.jsonl`` written by
  :mod:`predict_ngin.execution` brokers in dry-run, paper and live mode.
* **Collector heartbeat.** ``<data>/heartbeat/status.json`` (``ts``, ``ok``, ``problems``,
  ``services``), if a heartbeat job writes one.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from predict_ngin.shared.paths import data_root, repo_root

CONFIG = Path("config") / "paper_monitor.local.yaml"
STATUS_FILES = ("status.json", "summary.json", "state.json")
FILE_KINDS = (".jsonl", ".csv", ".json", ".parquet", ".txt", ".log")
TIME_COLUMNS = ("ts", "utc", "time", "timestamp", "receipt_utc", "last_run", "date", "day")
DEFAULT_STALE_HOURS = 6.0


@dataclass(frozen=True)
class Run:
    name: str
    dir: Path
    stale_hours: float = DEFAULT_STALE_HOURS
    source: str = "paper_trading"  # or "config"
    performance: Optional[str] = None  # JSON of a performance spec (see viz.performance.Spec)

    def perf_spec(self) -> Optional[dict]:
        return json.loads(self.performance) if self.performance else None


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_time(x) -> Optional[dt.datetime]:
    if x is None or x == "":
        return None
    try:
        t = pd.Timestamp(x)
    except (ValueError, TypeError):
        return None
    if pd.isna(t):
        return None
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


# ------------------------------------------------------------------------------------------ runners
def load_config(path: Optional[Path] = None) -> dict:
    p = path or repo_root() / CONFIG
    if not p.exists():
        return {}
    import yaml

    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def discover_runs(data: Optional[Path] = None, config: Optional[dict] = None) -> List[Run]:
    """Runner folders under ``<data>/paper_trading`` (with a status file) plus configured ones."""
    data = data or data_root()
    runs: List[Run] = []
    base = data / "paper_trading"
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if d.is_dir() and any((d / f).is_file() for f in STATUS_FILES):
                runs.append(Run(d.name, d))
    cfg = load_config() if config is None else config
    for r in cfg.get("runs") or []:
        d = Path(r["dir"])
        if not d.is_absolute():
            d = repo_root() / d
        perf = json.dumps(r["performance"]) if r.get("performance") else None
        same = [i for i, x in enumerate(runs) if x.dir.resolve() == d.resolve()]
        if same:  # a config entry for a discovered runner only adds settings
            x = runs[same[0]]
            runs[same[0]] = Run(
                str(r.get("name") or x.name),
                x.dir,
                float(r.get("stale_hours") or x.stale_hours),
                x.source,
                perf,
            )
            continue
        runs.append(
            Run(
                str(r.get("name") or d.name),
                d,
                float(r.get("stale_hours") or DEFAULT_STALE_HOURS),
                "config",
                perf,
            )
        )
    return runs


def read_status(run: Run) -> Tuple[Optional[str], dict]:
    """(file name, document) of the run's first status-like JSON object, or (None, {})."""
    for f in STATUS_FILES:
        p = run.dir / f
        if p.is_file():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict):
                return f, doc
    return None, {}


def run_files(run: Run, depth: int = 2, limit: int = 2000) -> pd.DataFrame:
    """Data files in the run folder (up to ``depth`` levels; newest first)."""
    rows = []
    if not run.dir.is_dir():
        return pd.DataFrame(columns=["path", "kind", "bytes", "modified"])
    base_depth = len(run.dir.parts)
    for root, dirs, files in os.walk(run.dir):
        if len(Path(root).parts) - base_depth >= depth:
            dirs[:] = []
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() not in FILE_KINDS:
                continue
            st = p.stat()
            rows.append(
                {
                    "path": p.relative_to(run.dir).as_posix(),
                    "kind": p.suffix.lower().lstrip("."),
                    "bytes": st.st_size,
                    "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc),
                }
            )
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break
    df = pd.DataFrame(rows, columns=["path", "kind", "bytes", "modified"])
    return df.sort_values("modified", ascending=False, ignore_index=True)


def health(run: Run, now: Optional[dt.datetime] = None) -> dict:
    """One row of the runner table: last activity, age, and whether it looks stale or failing."""
    now = now or _utc_now()
    fname, st = read_status(run)
    last = _parse_time(st.get("last_run")) or _parse_time(st.get("ts"))
    if last is None:
        files = run_files(run)
        last = files["modified"].iloc[0].to_pydatetime() if len(files) else None
    age_h = None if last is None else (now - last).total_seconds() / 3600
    nxt = _parse_time(st.get("next_boundary"))
    problems = []
    if not run.dir.is_dir():
        problems.append("folder missing")
    if st.get("ok") is False:
        problems.append("last pass not ok")
    if st.get("kill"):
        problems.append(f"kill: {st['kill']}")
    if age_h is None:
        problems.append("no files")
    elif age_h > run.stale_hours:
        problems.append(f"nothing new for {age_h:.1f} h")
    if nxt is not None and now > nxt + dt.timedelta(hours=1):
        problems.append("missed next boundary")
    return {
        "run": run.name,
        "mode": st.get("mode", "-"),
        "ok": not problems,
        "last_activity": last,
        "age_h": None if age_h is None else round(age_h, 2),
        "passes": st.get("passes"),
        "next": nxt,
        "problems": "; ".join(problems),
        "status_file": fname or "-",
        "folder": str(run.dir),
    }


def number_dicts(doc: dict) -> Dict[str, Dict[str, float]]:
    """Top-level fields that are flat dicts of numbers (P&L scenarios, equity, counters)."""
    out = {}
    for k, v in doc.items():
        if (
            isinstance(v, dict)
            and v
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v.values())
        ):
            out[k] = v
    return out


def table_dicts(doc: dict) -> Dict[str, pd.DataFrame]:
    """Top-level fields that are dicts of dicts (positions per instrument, ...) as tables."""
    out = {}
    for k, v in doc.items():
        if isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()):
            out[k] = pd.DataFrame.from_dict(v, orient="index")
    return out


def scalars(doc: dict) -> Dict[str, object]:
    return {k: v for k, v in doc.items() if not isinstance(v, (dict, list))}


# ------------------------------------------------------------------------------------------ files
def _plain(x):
    """JSON-able form of a nested value (numpy arrays from Parquet list columns included)."""
    import numpy as np

    if isinstance(x, np.ndarray):
        return [_plain(v) for v in x.tolist()]
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_plain(v) for v in x]
    return x


def hashable(df: pd.DataFrame) -> pd.DataFrame:
    """Nested values (dicts, lists, arrays, sets) as compact JSON text, so columns can be grouped,
    counted and shown (a dict cell made ``nunique`` raise "unhashable type: 'dict'")."""
    import numpy as np

    nested = (dict, list, tuple, set, np.ndarray)
    for c in df.columns:
        if df[c].dtype == object and df[c].map(lambda x: isinstance(x, nested)).any():
            df[c] = df[c].map(
                lambda x: json.dumps(_plain(x), default=str) if isinstance(x, nested) else x
            )
    return df


def tail_lines(path: Path, n: int) -> List[str]:
    """The last ``n`` lines of a text file, reading backwards in blocks."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        buf, pos = b"", end
        while pos > 0 and buf.count(b"\n") <= n:
            step = min(1 << 16, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
    lines = buf.decode("utf-8", errors="replace").splitlines()
    return [x for x in lines[-n:] if x.strip()]


def read_table(path: Path, n: int = 1000, max_level: Optional[int] = 1) -> pd.DataFrame:
    """The last ``n`` rows of a jsonl / csv / parquet / json file; nested JSON fields are flattened
    ``max_level`` levels deep (``None``: fully, as ``a.b.c``) and the rest kept as JSON text."""
    suf = path.suffix.lower()
    if suf == ".jsonl":
        rows = []
        for line in tail_lines(path, n):
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return hashable(pd.json_normalize(rows, max_level=max_level)) if rows else pd.DataFrame()
    if suf == ".csv":
        return hashable(pd.read_csv(path).tail(n).reset_index(drop=True))
    if suf == ".parquet":
        return hashable(pd.read_parquet(path).tail(n).reset_index(drop=True))
    if suf == ".json":
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, list):
            return hashable(pd.json_normalize(doc[-n:], max_level=max_level))
        if isinstance(doc, dict):
            return hashable(pd.json_normalize([doc], max_level=max_level))
        return pd.DataFrame({"value": [doc]})
    return pd.DataFrame({"line": tail_lines(path, n)})


def time_column(df: pd.DataFrame) -> Optional[str]:
    """The first column that looks like a timestamp, converted in place to UTC datetimes."""
    for c in TIME_COLUMNS:
        if c in df.columns:
            t = pd.to_datetime(df[c], utc=True, errors="coerce")
            if t.notna().any():
                df[c] = t
                return c
    return None


def numeric_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and df[c].dtype != bool]


# ------------------------------------------------------------------------------------------ orders
def journal_frame(data: Optional[Path] = None, days: int = 14) -> pd.DataFrame:
    """Order-journal events of the last ``days`` UTC days (all modes and venues)."""
    d = (data or data_root()) / "live" / "journal"
    if not d.is_dir():
        return pd.DataFrame()
    cutoff = (_utc_now() - dt.timedelta(days=days)).date().isoformat()
    rows = []
    for p in sorted(d.glob("*.jsonl")):
        if p.stem < cutoff:
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    if not rows:
        return pd.DataFrame()
    df = hashable(pd.json_normalize(rows, max_level=0))
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    return df.sort_values("ts", ignore_index=True)


def order_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Events per mode / venue / event type."""
    if df.empty:
        return pd.DataFrame(columns=["mode", "venue", "event", "n"])
    return (
        df.groupby(["mode", "venue", "event"])
        .size()
        .rename("n")
        .reset_index()
        .sort_values(["mode", "venue", "n"], ascending=[True, True, False], ignore_index=True)
    )


def fills(df: pd.DataFrame) -> pd.DataFrame:
    """Fill events with notional (price x quantity) and running totals per mode / venue."""
    if df.empty or "event" not in df.columns:
        return pd.DataFrame()
    f = df[df["event"] == "fill"].copy()
    if f.empty:
        return f
    f["notional"] = pd.to_numeric(f.get("price"), errors="coerce") * pd.to_numeric(
        f.get("quantity"), errors="coerce"
    )
    f["cum_notional"] = f.groupby(["mode", "venue"])["notional"].cumsum()
    f["cum_quantity"] = f.groupby(["mode", "venue"])["quantity"].cumsum()
    return f.reset_index(drop=True)


# --------------------------------------------------------------------------------------- heartbeat
def heartbeat(data: Optional[Path] = None) -> Tuple[dict, pd.DataFrame]:
    """(summary, service rows) from ``<data>/heartbeat/status.json``; ({}, empty) if absent."""
    p = (data or data_root()) / "heartbeat" / "status.json"
    if not p.is_file():
        return {}, pd.DataFrame()
    doc = json.loads(p.read_text(encoding="utf-8"))
    svc = pd.DataFrame.from_dict(doc.get("services") or {}, orient="index")
    svc.index.name = "service"
    meta = {k: v for k, v in doc.items() if k != "services"}
    return meta, svc.reset_index()
