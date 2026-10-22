"""Launcher for the SharpAPI odds recorder (collector registries need a script path).

    python -u scripts/live/sharpapi_record.py run --until 2026-10-20T03:10:00Z --stop-file-at-end

Same arguments as ``python -m predict_ngin.capture.sharpapi``; see that module and
docs/SHARPAPI_RECORDER.md.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from predict_ngin.capture.sharpapi import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
