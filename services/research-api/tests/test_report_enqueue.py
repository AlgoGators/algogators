"""Queueing the day's report: only when the book is published today and it is past 09:30 ET."""

import datetime as dt
from zoneinfo import ZoneInfo

from research_api.application.reports.enqueue import EnqueueDailyReports
from research_api.infrastructure.reports.worker import enabled, run_once

NY = ZoneInfo("America/New_York")
CFG = {
    "id": "qt",
    "name": "QT Conservative",
    "portfolio_id": "QT_CONSERVATIVE",
    "strategy_type": "S",
}


class Registry:
    def list(self):
        return [CFG]


class Reader:
    def __init__(self):
        self.calls = 0

    def fetch_detail_rows(self, strategy_type, portfolio_id):
        self.calls += 1
        return "rows"


class Published:
    def __init__(self, day):
        self.day = day

    def latest_published_day(self, portfolio_id):
        return self.day


class Queue:
    def __init__(self, existing=False):
        self.existing, self.rows = existing, []

    def has_report(self, report_type, portfolio_id, day):
        return self.existing

    def enqueue(self, report_type, portfolio_id, day, payload):
        self.rows.append((report_type, portfolio_id, day, payload))


def _detail(cfg, rows):
    import test_report_builder as t  # the same detail the builder test uses

    return t._detail()


def _run(now, published, queue=None, ids=("QT_CONSERVATIVE",)):
    queue = queue or Queue()
    reader = Reader()
    use = EnqueueDailyReports(
        Registry(), reader, Published(published), queue, ids, _detail, now=lambda: now
    )
    return use(), queue, reader


def test_a_published_book_is_queued_after_0930():
    summary, queue, _ = _run(dt.datetime(2026, 10, 22, 9, 31, tzinfo=NY), dt.date(2026, 10, 22))
    assert summary.queued == 1
    ((kind, pid, day, payload),) = queue.rows
    assert (kind, pid, day) == ("daily_trading", "QT_CONSERVATIVE", dt.date(2026, 10, 22))
    assert payload["portfolio_name"] == "QT Conservative" and payload["schema_version"] == 1


def test_nothing_is_queued_before_0930_even_if_published():
    summary, queue, reader = _run(
        dt.datetime(2026, 10, 22, 9, 29, tzinfo=NY), dt.date(2026, 10, 22)
    )
    assert summary.queued == 0 and queue.rows == [] and reader.calls == 0


def test_yesterdays_book_or_an_unpublished_day_is_not_queued():
    for published in (dt.date(2026, 10, 21), None):
        summary, queue, _ = _run(dt.datetime(2026, 10, 22, 10, 5, tzinfo=NY), published)
        assert summary.queued == 0 and queue.rows == []


def test_an_already_queued_day_is_not_queued_twice():
    summary, queue, reader = _run(
        dt.datetime(2026, 10, 22, 10, 5, tzinfo=NY), dt.date(2026, 10, 22), Queue(existing=True)
    )
    assert summary.queued == 0 and queue.rows == [] and reader.calls == 0


def test_no_configured_portfolio_means_nothing_is_queued():
    summary, queue, _ = _run(
        dt.datetime(2026, 10, 22, 10, 5, tzinfo=NY), dt.date(2026, 10, 22), ids=()
    )
    assert summary.queued == 0 and queue.rows == []


def test_the_worker_does_nothing_unless_enabled():
    assert not enabled({}) and not enabled({"REPORT_SEND_ENABLED": "true"})
    assert enabled({"REPORT_SEND_ENABLED": "1"})
    assert run_once({}) == 0  # idle: no database or SMTP is touched
