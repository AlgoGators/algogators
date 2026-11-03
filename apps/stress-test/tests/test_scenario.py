import numpy as np
import pandas as pd
import pytest
from stress_test.catalogue import Event
from stress_test.scenario import (
    PRICE_FLOOR_OF_HIST_LOW,
    calendar,
    daily_vol,
    event_moves,
    real_levels,
    translate,
)


def alternating(n: int, step: float, start: float = 100.0) -> np.ndarray:
    growth = np.where(np.arange(n) % 2 == 0, 1 + step, 1 - step)
    growth[0] = 1.0
    return start * np.cumprod(growth)


def event(start: pd.Timestamp, end: pd.Timestamp | None = None, named=("A",)) -> Event:
    return Event(
        id="X",
        name="x",
        klass="Minor",
        variant="",
        start=start,
        end=end if end is not None else start,
        single=end is None,
        instruments=tuple(named),
        measured=pd.DataFrame(),
    )


def test_daily_vol_uses_only_the_last_252_returns() -> None:
    noisy = np.r_[np.linspace(50, 150, 40), alternating(253, 0.01, start=150)]
    s = pd.Series(noisy, index=pd.bdate_range("2020-01-01", periods=len(noisy)))
    assert daily_vol(s) == pytest.approx(0.01, rel=1e-2)
    assert np.isnan(daily_vol(s.iloc[:2]))


def test_single_session_level_is_the_move_from_the_previous_close() -> None:
    idx = pd.bdate_range("2020-01-01", periods=5)
    s = pd.Series([100, 100, 120, 121, 122.0], index=idx)
    ev = event(idx[2])
    levels = real_levels(s, ev, calendar(ev, s.to_frame("A")))
    assert levels is not None and levels.tolist() == pytest.approx([0.2])
    # No bar on the day, or no recent previous bar: not covered.
    assert real_levels(s.drop(idx[2]), ev, pd.DatetimeIndex([idx[2]])) is None
    gappy = pd.Series([100.0, 120.0], index=[idx[2] - pd.Timedelta(30, unit="D"), idx[2]])
    assert real_levels(gappy, ev, pd.DatetimeIndex([idx[2]])) is None


def test_window_levels_are_relative_to_the_first_session_and_fill_gaps() -> None:
    idx = pd.bdate_range("2020-01-01", periods=6)
    a = pd.Series([90, 100, 98, 97, 101, 999.0], index=idx)
    b = a.drop(idx[3])  # b misses a session inside the window
    closes = pd.DataFrame({"A": a, "B": b})
    ev = event(idx[1], idx[4], named=("A", "B"))
    cal = calendar(ev, closes)
    assert list(cal) == list(idx[1:5])
    a_levels, b_levels = real_levels(closes["A"], ev, cal), real_levels(closes["B"], ev, cal)
    assert a_levels is not None and b_levels is not None
    assert a_levels.tolist() == pytest.approx([0, -0.02, -0.03, 0.01])
    assert b_levels.tolist() == pytest.approx([0, -0.02, -0.02, 0.01])
    # No bars inside the window at all: not covered.
    assert real_levels(a[a.index < idx[1]], ev, cal) is None


def test_translate_doubles_at_most_and_works_in_log_space() -> None:
    calm, wild = alternating(300, 0.01), alternating(300, 0.04)
    s = pd.Series(
        np.r_[calm, calm[-1] * wild / wild[0]], index=pd.bdate_range("2010-01-01", periods=600)
    )
    ev = event(s.index[300])
    k, levels, floored = translate(pd.Series([0.10]), s, ev)
    assert k == 2.0  # vol went up 4x; the rescale is capped at 2
    assert levels.iloc[0] == pytest.approx(1.10**2 - 1)
    assert not floored

    k_sev, levels_sev, _ = translate(pd.Series([0.10]), s, ev, severity=1.5)
    assert k_sev == 2.0 and levels_sev.iloc[0] == pytest.approx(1.10**3 - 1)


def test_translate_never_goes_below_the_realism_floor() -> None:
    s = pd.Series(alternating(600, 0.01), index=pd.bdate_range("2010-01-01", periods=600))
    floor = PRICE_FLOOR_OF_HIST_LOW * s.min() / s.iloc[-1] - 1
    k, levels, floored = translate(pd.Series([-0.9]), s, event(s.index[300]))
    assert k == pytest.approx(1.0, abs=1e-2)
    assert levels.iloc[0] == pytest.approx(floor)
    assert floored


def test_event_moves_reports_what_history_cannot_cover() -> None:
    idx = pd.bdate_range("2020-01-01", periods=400)
    closes = pd.DataFrame(
        {"A": alternating(400, 0.01), "LATE": np.r_[np.full(350, np.nan), alternating(50, 0.01)]},
        index=idx,
    )
    ev = event(idx[300])
    moves, uncovered = event_moves(ev, closes, ["A", "LATE", "MISSING"])
    assert set(moves) == {"A"}
    assert uncovered == ["LATE", "MISSING"]
