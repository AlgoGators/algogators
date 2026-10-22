"""Worker: queue today's daily reports when due and send what is queued.

    python scripts/send_reports.py            # one pass, then exit (cron, a timer)
    python scripts/send_reports.py --loop 60  # keep polling every 60 seconds

Same as `python -m research_api.infrastructure.reports.worker`; see that module for the settings.
Exits non-zero when a report failed.
"""

import sys

from research_api.infrastructure.reports.worker import main

if __name__ == "__main__":
    sys.exit(main())
