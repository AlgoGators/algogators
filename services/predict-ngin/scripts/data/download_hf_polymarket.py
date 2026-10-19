#!/usr/bin/env python3
"""
Download files of the Polymarket dataset from HuggingFace, pinned to the 2026-10-05 release.

Source: https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data (revision HF_REV in
predict_ngin/tapes/polymarket.py; data through 2026-10-04 UTC). The earlier Feb-2026 release is no
longer published, so this is the only release a new setup can get.

Files in the release:
  - markets.parquet      (546 MB)  - market metadata; the fill-tape build needs this one
  - trades.parquet       (46 GB)   - fills with market linkage; polymarket_extend.py reads it remotely
  - quant.parquet        (45 GB)   - clean trades, unified YES perspective
  - users.parquet        (61 GB)   - user behaviour, maker/taker split
  - orderfilled.parquet  (127 GB)  - raw OrderFilled events

Usage (repo root):
    # markets.parquet into <data root>/polymarket/hf_20261005/ (what the setup needs)
    uv run python scripts/data/download_hf_polymarket.py

    # specific files, or everything (~280 GB)
    uv run python scripts/data/download_hf_polymarket.py --files markets.parquet quant.parquet
    uv run python scripts/data/download_hf_polymarket.py --all

    # another directory
    uv run python scripts/data/download_hf_polymarket.py --output-dir D:/pm
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from predict_ngin.tapes.polymarket import HF_REPO, HF_REV, hf_snapshot_dir  # noqa: E402

FILE_SIZES_GB = {
    "markets.parquet": 0.55,
    "trades.parquet": 45.8,
    "quant.parquet": 44.7,
    "users.parquet": 60.8,
    "orderfilled.parquet": 127.2,
}
ALL_FILES = list(FILE_SIZES_GB)
ESSENTIAL_FILES = ["markets.parquet"]


def download_files(files: list[str], output_dir: Path, force: bool) -> None:
    """Download files from HuggingFace."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        sys.exit("huggingface_hub missing: run `uv sync` (it is a project dependency)")

    output_dir.mkdir(parents=True, exist_ok=True)
    total_start = time.time()

    for i, filename in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] Downloading {filename} ({FILE_SIZES_GB[filename]} GB)...")

        dest = output_dir / filename
        if dest.exists() and not force:
            existing_mb = dest.stat().st_size / 1024 / 1024
            print(f"  Already exists ({existing_mb:.0f} MB) - skipping. Use --force to re-download.")
            continue

        start = time.time()
        try:
            path = hf_hub_download(
                repo_id=HF_REPO,
                filename=filename,
                repo_type="dataset",
                revision=HF_REV,
                local_dir=str(output_dir),
                force_download=force,
            )
            elapsed = time.time() - start
            file_size = Path(path).stat().st_size / 1024 / 1024
            speed = file_size / elapsed if elapsed > 0 else 0
            print(f"  Done: {file_size:.0f} MB in {elapsed:.0f}s ({speed:.1f} MB/s)")
        except Exception as e:
            print(f"  ERROR: {e}")
            print(f"  Try: hf download {HF_REPO} {filename} --repo-type dataset --revision {HF_REV} "
                  f"--local-dir {output_dir}")

    total_elapsed = time.time() - total_start
    print(f"\nAll downloads complete in {total_elapsed / 60:.1f} minutes")

    # Summary
    print("\nFiles:")
    for f in files:
        p = output_dir / f
        if p.exists():
            size_mb = p.stat().st_size / 1024 / 1024
            if size_mb > 1024:
                print(f"  {f}: {size_mb / 1024:.1f} GB")
            else:
                print(f"  {f}: {size_mb:.0f} MB")
        else:
            print(f"  {f}: MISSING")


def main():
    parser = argparse.ArgumentParser(
        description="Download the pinned Polymarket dataset release from HuggingFace",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--all", action="store_true", help="Download all files (~280 GB)")
    parser.add_argument("--files", nargs="+", choices=ALL_FILES, help="Specific files to download")
    parser.add_argument("--output-dir", default=str(hf_snapshot_dir()),
                        help="Output directory (default: <data root>/polymarket/hf_20261005)")
    parser.add_argument("--force", action="store_true", help="Re-download existing files")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    files = args.files or (ALL_FILES if args.all else ESSENTIAL_FILES)
    total_gb = sum(FILE_SIZES_GB[f] for f in files)

    print("=" * 60)
    print("POLYMARKET HUGGINGFACE DATASET DOWNLOADER")
    print("=" * 60)
    print(f"Source: huggingface.co/datasets/{HF_REPO} @ {HF_REV[:12]}")
    print(f"Output: {output_dir}")
    print(f"Files:  {len(files)} ({total_gb:.1f} GB total)")
    print()
    for f in files:
        print(f"  - {f} ({FILE_SIZES_GB[f]} GB)")
    print("=" * 60)

    download_files(files, output_dir, args.force)


if __name__ == "__main__":
    main()
