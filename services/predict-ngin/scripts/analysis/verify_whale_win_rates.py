#!/usr/bin/env python3
"""
Look up whale historical win rates from research data (past resolved trades).

Use to verify whales before following: see their actual_win_rate, surprise_win_rate,
and sample size from past markets.

Usage:
    python scripts/analysis/verify_whale_win_rates.py 0xabc... 0xdef...
    python scripts/analysis/verify_whale_win_rates.py --file whales.txt
    echo 0xabc... | python scripts/analysis/verify_whale_win_rates.py --stdin
"""

import argparse
import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

from scripts.analysis.find_whale_opportunities import verify_whale_win_rates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Look up whale historical win rates from research data"
    )
    parser.add_argument("whales", nargs="*", help="Whale addresses (0x...)")
    parser.add_argument("--file", "-f", type=Path, help="File with one whale address per line")
    parser.add_argument("--stdin", action="store_true", help="Read whale addresses from stdin")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research")
    parser.add_argument("--min-trades", type=int, default=5, help="Min resolved trades to include")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    addresses = list(args.whales)
    if args.file and args.file.exists():
        addresses.extend(
            line.strip() for line in args.file.read_text().splitlines()
            if line.strip().startswith("0x")
        )
    if args.stdin:
        addresses.extend(
            line.strip() for line in sys.stdin
            if line.strip().startswith("0x")
        )

    addresses = [a.strip() for a in addresses if a and a.startswith("0x")]
    if not addresses:
        print("No whale addresses provided. Use: verify_whale_win_rates.py 0x... [0x...] or --file / --stdin")
        return 1

    stats = verify_whale_win_rates(addresses, args.research_dir, min_trades=args.min_trades)

    if args.json:
        def _ser(v):
            return float(v) if v is not None and hasattr(v, "item") else v
        out = {addr: {k: _ser(v) for k, v in s.items()} for addr, s in stats.items()}
        print(json.dumps(out, indent=2))
        return 0

    print("Whale historical win rates (from research resolved trades)\n")
    print(f"{'Address':<44} {'WR':>6} {'Exp':>6} {'Surprise':>8} {'n':>5}")
    print("-" * 75)
    for addr, s in stats.items():
        awr = s.get("actual_win_rate")
        ewr = s.get("expected_win_rate")
        swr = s.get("surprise_win_rate")
        n = s.get("sample_size", 0)
        awr_s = f"{awr*100:.1f}%" if awr is not None else "N/A"
        ewr_s = f"{ewr*100:.1f}%" if ewr is not None else "N/A"
        swr_s = f"{swr*100:+.1f}%" if swr is not None else "N/A"
        print(f"{addr[:42]:<44} {awr_s:>6} {ewr_s:>6} {swr_s:>8} {n:>5}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
