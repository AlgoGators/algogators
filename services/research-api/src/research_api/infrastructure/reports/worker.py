"""The report worker: queue today's reports when they are due, then send what is queued.

    python -m research_api.infrastructure.reports.worker --loop 60

It does nothing until REPORT_SEND_ENABLED=1, so deploying it changes nothing by itself. Settings:

    REPORT_SEND_ENABLED  "1" to queue and send; anything else leaves the worker idle
    REPORT_PORTFOLIOS    comma-separated portfolio ids to report on (e.g. the QT conservative book)
    REPORT_TO            comma-separated To addresses; QT_APPROVERS (vp, president) are Cc'd
    REPORT_RECORD_SENT   "1" to set live_run_metadata.sent_at after a send; ONLY with the engine's
                         QT_BOOK_EMAIL_HANDOFF=1, else the engine skips its own fund email
    REPORT_EXCLUDE       addresses never addressed (dhebrank@msn.com is built in)
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, REPORT_FROM   see infrastructure/reports/mailer.py

It exits non-zero when a report failed (single pass), so a scheduler can alert on it.
"""

import argparse
import logging
import os
import sys
import time

from research_api.application.portfolio.use_cases import build_strategy_detail
from research_api.application.reports.enqueue import EnqueueDailyReports
from research_api.application.reports.use_cases import SendQueuedReports
from research_api.domain.reports.recipients import parse_address_list
from research_api.infrastructure.investor.repositories import PostgresInvestorRepository
from research_api.infrastructure.portfolio.repositories import PostgresPortfolioRepository
from research_api.infrastructure.portfolio.strategy_registry import PostgresStrategyRegistry
from research_api.infrastructure.reports.book import PostgresBookSentMarker
from research_api.infrastructure.reports.holds import PostgresHeldDays
from research_api.infrastructure.reports.mailer import SmtpMailer
from research_api.infrastructure.reports.queue import PostgresReportQueue
from research_api.infrastructure.reports.recipients import EnvRecipients
from research_api.infrastructure.reports.renderer import HtmlReportRenderer

logger = logging.getLogger(__name__)


def enabled(env) -> bool:
    return (env.get("REPORT_SEND_ENABLED") or "").strip() == "1"


def record_sent(env) -> bool:
    """Whether a send also sets the book's sent_at. Off by default: with it on, the engine takes the
    day as e-mailed and skips its own send, so it is turned on only with QT_BOOK_EMAIL_HANDOFF."""
    return (env.get("REPORT_RECORD_SENT") or "").strip() == "1"


def run_once(env=None) -> int:
    """One pass; the number of reports that failed."""
    env = os.environ if env is None else env
    if not enabled(env):
        logger.info("REPORT_SEND_ENABLED is not 1: nothing queued, nothing sent")
        return 0
    who = EnvRecipients(env)
    queue = PostgresReportQueue()
    enqueue = EnqueueDailyReports(
        PostgresStrategyRegistry(),
        PostgresPortfolioRepository(),
        PostgresInvestorRepository(),
        queue,
        parse_address_list(env.get("REPORT_PORTFOLIOS")),
        build_strategy_detail,
        held=PostgresHeldDays(),
    )
    queued = enqueue()
    sent = SendQueuedReports(
        queue,
        HtmlReportRenderer(),
        SmtpMailer(env),
        who,
        who.approvers(),
        who.excluded(),
        book_sent=PostgresBookSentMarker() if record_sent(env) else None,
    )()
    logger.info(
        "queued=%d skipped=%d sent=%d failed=%d",
        queued.queued,
        queued.skipped,
        sent.sent,
        sent.failed,
    )
    return sent.failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, metavar="SECONDS", help="poll forever at this interval")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    failed_any = False
    while True:
        try:
            failed_any = run_once() > 0 or failed_any
        except Exception:
            logger.exception("report pass failed")
            failed_any = True
        if not args.loop:
            return 1 if failed_any else 0
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
