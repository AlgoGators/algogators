"""The book's sent_at is recorded only after the email really went out, and never undoes it."""

from research_api.application.reports.ports import QueuedReport
from research_api.application.reports.use_cases import SendQueuedReports


class Queue:
    def __init__(self):
        self.items = [QueuedReport(1, "daily_trading", "QT", (), {"report_date": "2026-10-22"}, 1)]
        self.sent, self.failed = [], []

    def claim_next(self):
        return self.items.pop(0) if self.items else None

    def mark_sent(self, report_id):
        self.sent.append(report_id)

    def mark_failed(self, report_id, error):
        self.failed.append(report_id)


class Renderer:
    def render_daily(self, report):
        return "email"


class Recipients:
    def to_emails(self):
        return ["qt@ufl.edu"]


class Mailer:
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    def send(self, to, cc, email):
        self.calls += 1
        if self.error:
            raise self.error


class Marker:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def mark_book_sent(self, portfolio_id, report_date):
        self.calls.append((portfolio_id, report_date))
        if self.error:
            raise self.error


def run(mailer, marker, monkeypatch):
    monkeypatch.setattr(
        "research_api.application.reports.use_cases.parse_daily_report", lambda payload: payload
    )
    queue = Queue()
    summary = SendQueuedReports(
        queue,
        Renderer(),
        mailer,
        Recipients(),
        {"vp": "vp@ufl.edu", "president": "p@ufl.edu"},
        book_sent=marker,
    )()
    return queue, summary


def test_marks_the_book_sent_after_a_successful_send(monkeypatch):
    marker = Marker()
    queue, summary = run(Mailer(), marker, monkeypatch)
    assert summary.sent == 1 and queue.sent == [1]
    assert marker.calls == [("QT", "2026-10-22")]


def test_a_failed_send_leaves_sent_at_alone(monkeypatch):
    marker = Marker()
    queue, summary = run(Mailer(RuntimeError("smtp down")), marker, monkeypatch)
    assert summary.failed == 1 and queue.failed == [1]
    assert marker.calls == []


def test_a_marker_failure_does_not_fail_or_resend_the_report(monkeypatch):
    mailer = Mailer()
    queue, summary = run(mailer, Marker(RuntimeError("no grant")), monkeypatch)
    assert summary.sent == 1 and summary.failed == 0
    assert queue.sent == [1] and queue.failed == [] and mailer.calls == 1
