"""Tests for the shared daily-return statistics."""

import pytest
from research_core.returns import compute_return_stats, compute_sharpe


def _series(*values: float) -> list[dict[str, float]]:
    return [{"value": value} for value in values]


class TestComputeReturnStats:
    def test_mixed_series(self) -> None:
        stats = compute_return_stats(_series(100, 120, 90, 108))

        assert stats["best_day"] == pytest.approx(20.0)
        assert stats["worst_day"] == pytest.approx(-25.0)
        # Peak 120 to trough 90.
        assert stats["max_drawdown"] == pytest.approx(25.0)
        # Two winning days (+20, +18) out of three.
        assert stats["win_rate"] == pytest.approx(200 / 3)
        assert stats["avg_win"] == pytest.approx(19.0)
        assert stats["avg_loss"] == pytest.approx(30.0)
        assert stats["profit_factor"] == pytest.approx(38 / 30)

    def test_empty_series_is_all_zeros(self) -> None:
        assert compute_return_stats([]) == {
            "best_day": 0,
            "worst_day": 0,
            "max_drawdown": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
        }

    def test_single_point_has_no_returns_or_drawdown(self) -> None:
        stats = compute_return_stats(_series(100))

        assert stats["best_day"] == 0
        assert stats["worst_day"] == 0
        assert stats["max_drawdown"] == 0
        assert stats["win_rate"] == 0

    def test_non_positive_previous_value_is_skipped(self) -> None:
        stats = compute_return_stats(_series(0, 50, 25))

        # The 0 -> 50 move produces no return; only 50 -> 25 counts.
        assert stats["best_day"] == pytest.approx(-50.0)
        assert stats["worst_day"] == pytest.approx(-50.0)
        assert stats["max_drawdown"] == pytest.approx(50.0)
        assert stats["win_rate"] == 0
        assert stats["avg_win"] == 0
        assert stats["avg_loss"] == pytest.approx(25.0)
        assert stats["profit_factor"] == 0

    def test_all_winning_days(self) -> None:
        stats = compute_return_stats(_series(100, 110, 121))

        assert stats["win_rate"] == pytest.approx(100.0)
        assert stats["max_drawdown"] == 0
        assert stats["avg_loss"] == 0
        # No gross loss means the ratio is reported as 0, not infinity.
        assert stats["profit_factor"] == 0

    def test_drawdown_tracks_new_peaks(self) -> None:
        stats = compute_return_stats(_series(100, 80, 200, 150))

        # Worst drawdown is from the later peak of 200 down to 150 (25%),
        # not the earlier 100 -> 80 dip (20%).
        assert stats["max_drawdown"] == pytest.approx(25.0)


class TestComputeSharpe:
    def test_positive_volatility(self) -> None:
        assert compute_sharpe(10.0, 5.0) == pytest.approx(2.0)

    def test_negative_return(self) -> None:
        assert compute_sharpe(-3.0, 2.0) == pytest.approx(-1.5)

    def test_zero_volatility_returns_zero(self) -> None:
        assert compute_sharpe(10.0, 0.0) == 0

    def test_negative_volatility_returns_zero(self) -> None:
        assert compute_sharpe(10.0, -1.0) == 0
