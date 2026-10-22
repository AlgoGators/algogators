"""Optional cold storage: move rarely used data folders to another drive and back, leaving a link at the old path.

By default there is no cold drive and nothing moves: all data stays in the repo's data folder
(``predict_ngin.shared.paths.data_root()``, i.e. ``$PREDICT_NGIN_DATA`` or ``<repo>/data``). A machine that has a
slower or bigger drive can set one, outside version control:

* ``PREDICT_NGIN_COLD=/path/to/archive``, or
* ``cold_root: /path/to/archive`` in ``config/storage.local.yaml`` (gitignored; template: config/storage.example.yaml)

``demote`` copies a data folder to ``<cold>/data/<folder>``, checks every file arrived with the same size, deletes the
original and leaves a link at the old path (a directory junction on Windows, a symlink elsewhere), so code keeps
reading the same path, only slower. ``promote`` brings it back to the fast drive before a long study. What is where is
recorded in ``<data>/storage_tiers.json``. Paths are relative to the data folder (a leading ``data/`` is accepted).

    uv run python scripts/data/storage_tier.py list
    uv run python scripts/data/storage_tier.py demote polymarket by_category
    uv run python scripts/data/storage_tier.py promote by_category
    uv run python scripts/data/storage_tier.py demote polymarket --cold /mnt/archive/predict-ngin   # one-off override
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from predict_ngin.shared.paths import cold_root, data_root  # noqa: E402


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%H:%M:%S} {msg}", flush=True)


def manifest_path() -> Path:
    return data_root() / "storage_tiers.json"


def load_manifest() -> dict:
    p = manifest_path()
    m = json.loads(p.read_text()) if p.exists() else {}
    # entries written before 2026-10-17 were keyed "data/<folder>"
    return {k[5:] if k.startswith("data/") else k: v for k, v in m.items()}


def save_manifest(m: dict) -> None:
    manifest_path().write_text(json.dumps(m, indent=1, sort_keys=True))


def is_link(p: Path) -> bool:
    """True for a symlink or a Windows directory junction."""
    if p.is_symlink():
        return True
    is_junction = getattr(p, "is_junction", None)  # Python >= 3.12
    if is_junction:
        return bool(is_junction())
    try:  # Python 3.11: a junction is a reparse point that is not a symlink
        attrs = getattr(os.lstat(p), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def make_link(link: Path, target: Path) -> None:
    if os.name == "nt":  # junctions need no admin rights or developer mode, unlike symlinks
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def remove_link(link: Path) -> None:
    """Remove the link itself, never what it points to."""
    if os.name == "nt" and not link.is_symlink():
        os.rmdir(link)  # a junction
    else:
        link.unlink()


def inventory(d: Path) -> tuple[int, int]:
    n = size = 0
    for dirpath, _, files in os.walk(d):
        for f in files:
            n += 1
            size += os.path.getsize(os.path.join(dirpath, f))
    return n, size


def copy_verified(src: Path, dst: Path) -> tuple[int, int]:
    """Copy src -> dst via a temporary name, check file count and bytes, then rename into place."""
    part = dst.with_name(dst.name + ".partial")
    if part.exists():
        shutil.rmtree(part)
    part.parent.mkdir(parents=True, exist_ok=True)
    want = inventory(src)
    log(f"copy {src} -> {dst}: {want[0]:,} files, {want[1] / 1e9:.1f} GB")
    shutil.copytree(src, part, copy_function=shutil.copy2)
    got = inventory(part)
    if got != want:
        raise SystemExit(f"verify failed for {src}: source {want}, copy {got}; source left untouched")
    os.replace(part, dst)
    return want


def norm(rel: str) -> str:
    rel = Path(rel).as_posix().strip("/")
    return rel[5:] if rel.startswith("data/") else rel


def demote(rel: str, cold: Path) -> None:
    src = data_root() / rel
    if is_link(src):
        log(f"{rel} is already linked to {os.path.realpath(src)}; skipped")
        return
    if not src.is_dir():
        raise SystemExit(f"{src} is not a directory")
    dst = cold / "data" / rel
    if dst.exists():
        raise SystemExit(f"{dst} already exists; resolve it by hand")
    n, size = copy_verified(src, dst)
    shutil.rmtree(src)
    make_link(src, dst)
    m = load_manifest()
    m[rel] = {"tier": "cold", "path": str(dst), "files": n, "bytes": size, "at": dt.datetime.now().isoformat()}
    save_manifest(m)
    log(f"demoted {rel}: {size / 1e9:.1f} GB now under {cold}, link left at {src}")


def promote(rel: str) -> None:
    link = data_root() / rel
    m = load_manifest()
    if not is_link(link) or m.get(rel, {}).get("tier") != "cold":
        raise SystemExit(f"{rel} is not a demoted folder")
    cold = Path(m[rel]["path"])
    tmp = link.with_name(link.name + ".promoting")
    n, size = copy_verified(cold, tmp)
    remove_link(link)
    os.replace(tmp, link)
    shutil.rmtree(cold)
    m[rel] = {"tier": "hot", "files": n, "bytes": size, "at": dt.datetime.now().isoformat()}
    save_manifest(m)
    log(f"promoted {rel}: {size / 1e9:.1f} GB back in {data_root()}; cold copy removed")


def show(cold: Optional[Path]) -> None:
    print(f"data folder: {data_root()}")
    print(f"cold drive:  {cold if cold else 'not configured (everything stays in the data folder)'}")
    for rel, e in sorted(load_manifest().items()):
        ok = "" if e["tier"] == "hot" or Path(e["path"]).exists() else "  MISSING (is the drive attached?)"
        print(f"  {rel:38s} {e['tier']:5s} {e['bytes'] / 1e9:8.1f} GB  {e.get('path', '')}{ok}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["list", "demote", "promote"])
    ap.add_argument("paths", nargs="*", help="folders relative to the data folder, e.g. polymarket")
    ap.add_argument("--cold", help="cold root for this run (overrides PREDICT_NGIN_COLD / config/storage.local.yaml)")
    a = ap.parse_args(argv)
    cold = Path(a.cold) if a.cold else cold_root()
    if a.cmd == "list":
        show(cold)
        return 0
    for rel in map(norm, a.paths):
        if a.cmd == "demote":
            if cold is None:
                log(f"no cold drive configured; {rel} stays in {data_root()} (set PREDICT_NGIN_COLD or "
                    "config/storage.local.yaml to use one)")
                continue
            demote(rel, cold)
        else:
            promote(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
