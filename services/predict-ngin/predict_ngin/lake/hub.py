"""Share the Polymarket data as a Hugging Face dataset snapshot, laid out like the data root.

The snapshot holds exactly what ``predict-ngin data update polymarket`` needs to carry on from it:

* ``lake/polymarket/``: fills, 1h / 1m bars, markets, ``fills_state.json``
* ``polymarket_clean/trades_ext/``: the ext tape files the lake has not retired (``hf/`` parts and
  ``tail/`` days), their manifests, ``coverage.json``
* ``polymarket/hf_20261005/``: the market lists the tail fetch uses to map tokens
* ``polymarket_clean/event_tags.parquet``: Gamma event tags, so markets new since the snapshot get
  the same tag-based categories as the maintainer's build
* ``lake/polymarket/_manifest_polymarket.json``: the Polymarket entries of ``lake/_manifest.json``
  (``data status`` reads them); ``pull`` merges them into the local manifest

``publish`` (maintainer) hard-links those files into a staging folder, checks every lake month
against ``fills_state.json``, and uploads them in **one commit** that also deletes remote files no
longer present, so a download never mixes two snapshots. ``pull`` (everyone else) downloads the
newest commit into the data root, removes lake files a newer snapshot replaced, checks the months
again, then runs the normal update: Polygon RPC days since the snapshot, then a rebuild of the lake
months that changed.

Kalshi data is never published: Kalshi's Developer Agreement (3.1) and Data Terms forbid sharing
API data with third parties without written permission.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import shutil
from pathlib import Path
from typing import Iterable, List, Optional

from ..shared.lock import SingleInstance
from ..shared.paths import data_root
from ..tapes import polymarket as tape
from . import manifest

REPO_ENV_VAR = "PREDICT_NGIN_HF_REPO"
DEFAULT_REPO = "domdd/predict_ngin"
REPO_TYPE = "dataset"
STAGING = ".hub_publish"  # under the data root (same volume, so files are hard links, not copies)
MARKER = "lake/polymarket/.hub_snapshot.json"  # written by pull: this lake came from the Hub
MANIFEST = "lake/polymarket/_manifest_polymarket.json"  # written into the snapshot by publish

# what a snapshot holds, relative to the data root (fnmatch: '*' also matches '/')
PUBLISH = [
    "lake/polymarket/fills/year=*/month=*/part-*.parquet",
    "lake/polymarket/bars_1h/year=*/month=*/part-*.parquet",
    "lake/polymarket/bars_1m/year=*/month=*/part-*.parquet",
    "lake/polymarket/markets.parquet",
    "lake/polymarket/fills_state.json",
    "polymarket_clean/trades_ext/hf/part-rg*.parquet",
    "polymarket_clean/trades_ext/hf/_manifest/part-rg*.json",
    "polymarket_clean/trades_ext/tail/fills_????-??-??.parquet",
    "polymarket_clean/trades_ext/coverage.json",
    "polymarket_clean/trades_ext/hf_plan.json",
    f"polymarket/{tape.HF_SNAPSHOT}/markets.parquet",
    f"polymarket/{tape.HF_SNAPSHOT}/markets_gamma_tail.parquet",
    "polymarket_clean/event_tags.parquet",
]
# the remote prefixes a publish owns: anything there that the new snapshot lacks is deleted
OWNED = [
    "lake/polymarket/*",
    "polymarket_clean/trades_ext/*",
    "polymarket_clean/event_tags.parquet",
    f"polymarket/{tape.HF_SNAPSHOT}/*",
]
LAKE_PARTS = ("fills", "bars_1h", "bars_1m")


def repo_id(repo: Optional[str] = None) -> str:
    return repo or os.environ.get(REPO_ENV_VAR, "").strip() or DEFAULT_REPO


def log(msg: str) -> None:
    print(f"{dt.datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def _api():
    from huggingface_hub import HfApi

    return HfApi()


def _months(state: dict) -> List[str]:
    return sorted(k for k in state if not k.startswith("_"))


def check_lake(root: Path) -> List[str]:
    """Problems found comparing each lake fill month under ``root`` with ``fills_state.json``
    (file count and row count from parquet footers; no data is read). Empty list = consistent."""
    import pyarrow.parquet as pq

    state_p = root / "lake/polymarket/fills_state.json"
    if not state_p.exists():
        return [f"{state_p} missing"]
    state = json.loads(state_p.read_text())
    bad = []
    if not state.get("_complete"):
        bad.append("fills_state.json: lake build not complete")
    for m in _months(state):
        y, mo = m.split("-")
        files = sorted((root / f"lake/polymarket/fills/year={y}/month={mo}").glob("part-*.parquet"))
        rows = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
        want = state[m]
        if len(files) != want.get("files", len(files)) or rows != want["rows"]:
            bad.append(f"{m}: {len(files)} files / {rows:,} rows, state says "
                       f"{want.get('files')} / {want['rows']:,}")
    return bad


def snapshot_files(root: Path) -> List[str]:
    """Data-root-relative paths (posix) of the files a snapshot holds."""
    out = []
    for pat in PUBLISH:
        out += [p.relative_to(root).as_posix() for p in root.glob(pat) if p.is_file()]
    return sorted(set(out))


def dataset_card(root: Path, repo: str) -> str:
    state = json.loads((root / "lake/polymarket/fills_state.json").read_text())
    months = _months(state)
    rows = sum(state[m]["rows"] for m in months)
    usd = sum(state[m]["usd"] for m in months)
    end = dt.datetime.fromtimestamp(state["_source_end_ts"], dt.timezone.utc).strftime("%Y-%m-%d")
    return f"""---
license: mit
pretty_name: Polymarket on-chain fills (predict-ngin)
tags: [polymarket, prediction-markets, finance, polygon, trades]
size_categories: [1B<n<10B]
configs:
- config_name: fills
  data_files: "lake/polymarket/fills/*/*/*.parquet"
  default: true
- config_name: bars_1h
  data_files: "lake/polymarket/bars_1h/*/*/*.parquet"
- config_name: bars_1m
  data_files: "lake/polymarket/bars_1m/*/*/*.parquet"
- config_name: markets
  data_files: "lake/polymarket/markets.parquet"
---

# Polymarket on-chain fills

Every Polymarket fill on Polygon from {months[0]} to {end} (exclusive): {rows:,} rows,
${usd / 1e9:,.1f}B notional, YES-normalised (each fill priced as the YES outcome), sorted by
category, event, market and time inside monthly folders. Also hourly and 1-minute OHLC / VWAP
bars per market, and a market table with categories.

Built by [predict-ngin](https://github.com/AlgoGators/algogators/tree/main/services/predict-ngin)
from the
[SII-WANGZJ/Polymarket_data](https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data)
releases (MIT) and, after them, Polygon `OrderFilled` logs read from public RPC nodes.

## Get it

With the predict-ngin repository (downloads into your data folder, then fetches the days since this
snapshot and updates the lake):

```bash
uv run predict-ngin data pull polymarket
```

Without it:

```bash
hf download {repo} --type dataset --local-dir predict-data
```

or query it in place with DuckDB:

```sql
SELECT count(*) FROM 'hf://datasets/{repo}/lake/polymarket/fills/year=2026/month=*/*.parquet';
```

## Layout

| Path | Contents |
|---|---|
| `lake/polymarket/fills/year=YYYY/month=MM/` | fills; `fills_state.json` has per-month row counts |
| `lake/polymarket/bars_1h/`, `bars_1m/` | per-market bars from maker fills |
| `lake/polymarket/markets.parquet` | one row per market: question, event, tags, category |
| `polymarket_clean/trades_ext/` | source files predict-ngin uses to keep recent months current |
| `polymarket/{tape.HF_SNAPSHOT}/` | market lists used to map token ids to markets |
| `polymarket_clean/event_tags.parquet` | Gamma event tags behind the market categories |

## Notes

- **Count volume with `NOT is_taker_order_row`.** Rows from before 2025-12-30 include the taker
  order's aggregate rows; counting them double-counts volume.
- **`ts_chain`** is the block time. Before 2025-12-30 it comes from Polygon block anchors, because
  the source release's own timestamps there are off by minutes to hours; `ts_local` keeps the
  release value. `timestamp_unreliable` flags rows with no anchored time.
- `yes_price` and `yes_side` are YES-normalised; `price_raw` is the traded token's price.
- Wallet addresses (`maker`, `taker`) are public on-chain data.
- No Kalshi data is included.
"""


# --------------------------------------------------------------------------------------- publish
def _stage(root: Path, files: Iterable[str]) -> Path:
    stage = root / STAGING
    if stage.exists():
        shutil.rmtree(stage)
    linked = copied = 0
    for rel in files:
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(root / rel, dst)  # a frozen view: later rebuilds replace files, not edit them
            linked += 1
        except OSError:  # another volume (a cold-storage junction): copy
            shutil.copy2(root / rel, dst)
            copied += 1
    log(f"staged {linked} hard links + {copied} copies in {stage}")
    return stage


def snapshot_manifest() -> dict:
    """The Polymarket entries of the local lake manifest (never another venue's)."""
    return {k: v for k, v in manifest.load().items() if k.startswith("polymarket/")}


def publish(repo: Optional[str] = None, dry_run: bool = False) -> int:
    root, repo = data_root(), repo_id(repo)
    if not tape.lake_ready():
        print("the Polymarket lake is not complete here; run `predict-ngin data update polymarket`")
        return 1
    tail_lock = tape.ext_dir() / ".tail.lock"
    if tail_lock.exists():
        print(f"{tail_lock} exists: a tail fetch is running (or crashed; delete the lock)")
        return 1
    with SingleInstance(root / "lake" / ".lock_polymarket", "a Polymarket lake build"):
        bad = check_lake(root)
        if bad:
            print("lake does not match fills_state.json; not publishing:\n  " + "\n  ".join(bad))
            return 1
        files = snapshot_files(root)
        size = sum((root / f).stat().st_size for f in files)
        log(f"{len(files)} files, {size / 1e9:.1f} GB -> {repo}")
        if dry_run:
            for pat in PUBLISH:
                n = [f for f in files if fnmatch.fnmatch(f, pat)]
                print(f"  {len(n):5d}  {pat}")
            return 0
        stage = _stage(root, files)  # the lake lock is released after this; builds may resume
        (stage / MANIFEST).write_text(json.dumps(snapshot_manifest(), indent=1, sort_keys=True))
    try:
        (stage / "README.md").write_text(dataset_card(stage, repo), encoding="utf-8")
        api = _api()
        api.create_repo(repo, repo_type=REPO_TYPE, exist_ok=True)
        state = json.loads((stage / "lake/polymarket/fills_state.json").read_text())
        end = dt.datetime.fromtimestamp(state["_source_end_ts"], dt.timezone.utc).date()
        log("uploading (unchanged files are skipped; re-run to resume after an interruption)")
        info = api.upload_folder(
            repo_id=repo,
            repo_type=REPO_TYPE,
            folder_path=stage,
            delete_patterns=OWNED,  # one commit: new files in, files the snapshot lacks out
            commit_message=f"Polymarket snapshot through {end}",
        )
        log(f"published {info.oid[:12]}: https://huggingface.co/datasets/{repo}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return 0


# ------------------------------------------------------------------------------------------ pull
def _prune(root: Path, keep: set) -> int:
    """Delete local lake part files in month folders the snapshot has, when the snapshot lacks
    them (a newer build split the month differently); otherwise they would duplicate rows."""
    n = 0
    for part in LAKE_PARTS:
        months = {Path(f).parent for f in keep if f.startswith(f"lake/polymarket/{part}/")}
        for mdir in months:
            for f in (root / mdir).glob("part-*.parquet"):
                if f.relative_to(root).as_posix() not in keep:
                    f.unlink()
                    n += 1
    return n


def pull(repo: Optional[str] = None, update: bool = True, force: bool = False) -> int:
    """Download the newest snapshot (when it is newer than the local lake), check it, then catch up
    from it with the normal update."""
    from huggingface_hub import hf_hub_download, snapshot_download
    from huggingface_hub.errors import EntryNotFoundError

    from .cli import update_polymarket  # the same catch-up `data update polymarket` runs

    root, repo = data_root(), repo_id(repo)
    local_state = root / "lake/polymarket/fills_state.json"
    marker = root / MARKER
    api = _api()
    sha = api.dataset_info(repo).sha
    try:
        p = hf_hub_download(repo, "lake/polymarket/fills_state.json", repo_type=REPO_TYPE,
                            revision=sha)
    except EntryNotFoundError:
        print(f"{repo} has no Polymarket snapshot yet; build the data yourself "
              "(QUICK_START.md, step 3)")
        return 1
    remote = json.loads(Path(p).read_text())
    remote_end = int(remote["_source_end_ts"])
    local_end = (int(json.loads(local_state.read_text()).get("_source_end_ts", 0))
                 if local_state.exists() else 0)
    if local_state.exists() and not marker.exists() and not force:
        log(f"{local_state.parent} was built here, not pulled; keeping it (--force replaces it)")
    elif local_end >= remote_end and not force:
        log(f"local lake (to {_day(local_end)}) is as new as the snapshot ({_day(remote_end)})")
    else:
        files = [f for f in api.list_repo_files(repo, repo_type=REPO_TYPE, revision=sha)
                 if any(fnmatch.fnmatch(f, p) for p in OWNED)]
        log(f"downloading {repo}@{sha[:12]} (through {_day(remote_end)}): {len(files)} files "
            f"into {root}; unchanged files are skipped, re-run to resume")
        snapshot_download(repo, repo_type=REPO_TYPE, revision=sha, local_dir=root,
                          allow_patterns=OWNED, max_workers=8)
        pruned = _prune(root, set(files))
        if pruned:
            log(f"removed {pruned} local lake files the snapshot replaced")
        bad = check_lake(root)
        if bad:
            print("downloaded lake does not match fills_state.json:\n  " + "\n  ".join(bad))
            return 1
        if (root / MANIFEST).exists():  # so `data status` sees the pulled markets and fills
            manifest.put(json.loads((root / MANIFEST).read_text()))
        marker.write_text(json.dumps({"repo": repo, "revision": sha, "source_end_ts": remote_end,
                                      "pulled": dt.datetime.now(dt.timezone.utc).isoformat()}))
        log("snapshot checked: every month matches fills_state.json")
    if not update:
        return 0
    log("catching up: Polygon RPC days since the snapshot, then the lake months that changed")
    return update_polymarket(skip_fetch=False)


def _day(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d") if ts else "-"


__all__ = ["publish", "pull", "check_lake", "snapshot_files", "dataset_card", "repo_id"]
