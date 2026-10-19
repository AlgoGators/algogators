"""AimdLimiter: additive increase on success, multiplicative decrease on throttle, Retry-After,
latency-spike detection, and the ReadOnlyClient feedback hook."""

import pytest

from predict_ngin.shared.ratelimit import AimdLimiter
from predict_ngin.venues.http import ReadOnlyClient


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def lim(rate=10.0, **kw):
    c = Clock()
    return AimdLimiter(rate, clock=c, sleep=c.sleep, **kw), c


def test_paces_at_rate():
    a, c = lim(10.0)
    for _ in range(11):
        a.acquire()
    assert c.t == pytest.approx(1.0)


def test_additive_increase_is_about_increase_per_second():
    a, _ = lim(10.0, increase=1.0, max_rate=100)
    for _ in range(10):  # one second of traffic at 10/s
        a.success()
    assert a.rate == pytest.approx(11.0, abs=0.05)


def test_increase_caps_at_max_rate():
    a, _ = lim(10.0, max_rate=12)
    for _ in range(1000):
        a.success()
    assert a.rate == 12


def test_throttle_halves_once_per_cooldown():
    a, c = lim(16.0, cooldown=1.0)
    a.throttle()
    a.throttle()  # same window: in-flight requests reporting the same congestion
    assert a.rate == 8.0 and a.n_cuts == 1
    c.t += 1.0
    a.throttle()
    assert a.rate == 4.0


def test_floor_at_min_rate():
    a, c = lim(1.0, min_rate=0.5, cooldown=0)
    for _ in range(5):
        a.throttle()
    assert a.rate == 0.5


def test_retry_after_holds_callers():
    a, c = lim(100.0)
    a.throttle(retry_after=5.0)
    a.acquire()
    assert c.t >= 5.0


def test_feedback_maps_statuses():
    a, _ = lim(10.0)
    a.feedback(200)
    a.feedback(404)
    assert a.n_ok == 2
    a.feedback(500)
    a.feedback(None)
    assert a.n_ok == 2 and a.n_throttled == 0
    a.feedback(429)
    a.feedback(503)
    assert a.n_throttled == 2


def test_latency_spike_counts_as_throttle():
    a, _ = lim(10.0, slow_factor=3.0)
    for _ in range(20):
        a.success(0.1)
    before = a.rate
    for _ in range(10):
        a.success(1.0)
    assert a.rate < before and a.n_cuts >= 1


def test_latency_ignored_without_slow_factor():
    a, _ = lim(10.0)
    a.success(0.1)
    a.success(100.0)
    assert a.n_cuts == 0


class _Resp:
    def __init__(self, code, headers=None):
        self.status_code, self.headers = code, headers or {}

    def json(self):
        return {"ok": True}


class _Session:
    def __init__(self, codes):
        self.codes, self.headers = list(codes), {}

    def get(self, url, params=None, headers=None, timeout=None):
        return _Resp(self.codes.pop(0), {"Retry-After": "2"} if self.codes else None)


def test_read_only_client_reports_to_limiter():
    a, c = lim(10.0, cooldown=0)
    cl = ReadOnlyClient("http://x", session=_Session([429, 200]), limiter=a, sleep=c.sleep)
    assert cl.get("/p") == {"ok": True}
    assert a.n_throttled == 1 and a.n_ok == 1 and a.n_cuts == 1


def test_probes_slowly_near_last_throttled_rate():
    a, c = lim(20.0, cooldown=0, increase=1.0, max_rate=100)
    a.throttle()  # limit hit at 20/s
    assert a.ceiling == 20.0 and a.rate == 10.0
    for _ in range(200):  # fast recovery up to 0.9 x ceiling, then slow
        a.success()
    assert 18.0 <= a.rate < 19.5
    free, _ = lim(10.0, increase=1.0, max_rate=100)
    for _ in range(200):
        free.success()
    assert free.rate > 20  # no ceiling: plain additive increase, sqrt(10**2 + 2 * 200) ~ 22.4


def test_tolerance_ignores_stray_throttles():
    a, c = lim(10.0, cooldown=0, tolerance=0.10, window=20)
    for _ in range(19):
        a.success()
    a.throttle()  # 1 in 20 = 5%: noise, no cut
    assert a.n_cuts == 0
    for _ in range(3):
        a.throttle()  # 4 in 20 = 20% > 10%: cut
    assert a.n_cuts >= 1
