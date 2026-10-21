"""Worker: send the reports trade-ngin has queued in reports.report_queue.

    python scripts/send_reports.py            # drain the queue once and exit (cron, a timer)
    python scripts/send_reports.py --loop 60  # keep polling every 60 seconds

Exits non-zero when any report failed, so a cron wrapper or a scheduler can alert on it.
"""

import argparse
import logging
import sys
import time

from research_api.application.reports.use_cases import SendQueuedReports
from research_api.infrastructure.reports.mailer import SmtpMailer
from research_api.infrastructure.reports.queue import PostgresReportQueue
from research_api.infrastructure.reports.recipients import EnvRecipients
from research_api.infrastructure.reports.renderer import HtmlReportRenderer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, metavar="SECONDS", help="poll forever at this interval")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    who = EnvRecipients()
    send = SendQueuedReports(
        PostgresReportQueue(),
        HtmlReportRenderer(),
        SmtpMailer(),
        who,
        who.approvers(),
        who.excluded(),
    )
    failed_any = False
    while True:
        summary = send()
        logging.info("sent=%d failed=%d", summary.sent, summary.failed)
        failed_any = failed_any or summary.failed > 0
        if not args.loop:
            return 1 if failed_any else 0
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
