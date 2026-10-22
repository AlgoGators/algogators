"""Queue the day's report once the day's book is published.

AlgoLens builds the report itself from the same detail its screens show, so nothing has to enqueue
it from outside. A report is due when the portfolio's book for today has been published AND it is
past the 09:30 New York send time: until then the desk can still rescind a published book
(contract C8), and a report must never describe a book that can be taken back.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from research_api.domain.reports.builder import build_daily_payload

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
SEND_TIME = dt.time(9, 30)
REPORT_TYPE = "daily_trading"


@dataclass(frozen=True)
class EnqueueSummary:
    queued: int
    skipped: int


class EnqueueDailyReports:
    """For each configured portfolio, queue today's report if it is due and not queued yet.

    registry:   lists the live portfolios (PostgresStrategyRegistry.list)
    reader:     fetch_detail_rows(strategy_type, portfolio_id) (the portfolio repository)
    published:  latest_published_day(portfolio_id) -> date | None (the investor repository)
    queue:      has_report(...) and enqueue(...) (PostgresReportQueue)
    held:       held(portfolio_id, day) -> bool, a day approved without its e-mail (D-66) that the
                desk has not released with Send yet; it is not queued until it is
    """

    def __init__(
        self, registry, reader, published, queue, portfolio_ids, build_detail, now=None, held=None
    ):
        self.registry = registry
        self.reader = reader
        self.published = published
        self.queue = queue
        self.portfolio_ids = tuple(portfolio_ids)
        self.build_detail = build_detail  # (cfg, rows) -> detail dict | None
        self.held = held or (lambda portfolio_id, day: False)
        self.now = now or (lambda: dt.datetime.now(NEW_YORK))

    def __call__(self):
        now = self.now().astimezone(NEW_YORK)
        today = now.date()
        queued = skipped = 0
        if now.time() < SEND_TIME or not self.portfolio_ids:
            return EnqueueSummary(0, len(self.portfolio_ids))
        by_portfolio = {cfg["portfolio_id"]: cfg for cfg in self.registry.list()}
        for pid in self.portfolio_ids:
            cfg = by_portfolio.get(pid)
            if cfg is None:
                logger.warning("report portfolio %s is not in the registry", pid)
                skipped += 1
                continue
            day = self.published.latest_published_day(pid)
            if day != today or self.queue.has_report(REPORT_TYPE, pid, today):
                skipped += 1
                continue
            if self.held(pid, today):
                skipped += 1
                continue
            rows = self.reader.fetch_detail_rows(cfg["strategy_type"], pid)
            detail = self.build_detail(cfg, rows)
            if detail is None:
                skipped += 1
                continue
            payload = build_daily_payload(detail, pid, cfg["name"], today)
            self.queue.enqueue(REPORT_TYPE, pid, today, payload)
            queued += 1
        return EnqueueSummary(queued, skipped)
