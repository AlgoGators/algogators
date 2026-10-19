"""Launcher for The Odds API snapshot collector (registries need a script path).

    python -I scripts/live/oddsapi_snapshot.py --sport basketball_nba

Same arguments as ``python -m predict_ngin.capture.oddsapi``; see that module.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from predict_ngin.capture.oddsapi import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
