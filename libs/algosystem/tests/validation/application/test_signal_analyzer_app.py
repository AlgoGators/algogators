"""Tests for the SignalAnalyzer application use case.

Uses a deterministic in-test PassRunner fake (no multiprocessing, no
infrastructure adapters) and synthetic return series so every stage of the
pipeline -- detector, PSR/DSR, PBO, walk-forward, trial tracker, verdict --
is exercised with controlled inputs.
"""

from __future__ import annotations

import numpy as np
import pytest
from algosystem.shared.errors import ValidationError
from algosystem.validation.application.signal_analyzer import SignalAnalyzer
from algosystem.validation.domain.results import OverfitResults
from algosystem.validation.domain.statistics.cscv import PBOResults
from algosystem.validation.domain.statistics.psr_dsr import DSRResult, PSRResult, TrialTracker
from algosystem.validation.domain.statistics.signal_analyzer import SignalAnalysisReport
from algosystem.validation.domain.statistics.walkforward import WalkForwardResults

# ---------------------------------------------------------------------------
# Synthetic strategies and fakes
# ---------------------------------------------------------------------------


def momentum_backtest(params, returns):
    """Order-dependent momentum Sharpe so permutations change scores."""
    lookback = int(params["lookback"])
    threshold = float(params["threshold"])
    series = np.asarray(returns, dtype=np.float64)
    n = series.size
    if n <= lookback + 2:
        return 0.0
    cumulative = np.cumsum(series)
    momentum = np.empty(n)
    momentum[:lookback] = 0.0
    momentum[lookback:] = (cumulative[lookback:] - cumulative[:-lookback]) / lookback
    strategy = ((momentum > threshold).astype(float) * series)[lookback:]
    std = float(np.std(strategy, ddof=1))
    if std < 1e-12:
        return 0.0
    return float(np.mean(strategy) / std * np.sqrt(252.0))


def zero_backtest(params, returns):
    """Degenerate strategy that always scores zero."""
    return 0.0


class _EvaluatingRunner:
    """PassRunner fake: evaluates the backtest in-process, deterministically."""

    def __init__(self, evaluator=momentum_backtest):
        self.evaluator = evaluator
        self.calls: list[dict] = []

    def run_passes(
        self,
        strategy,
        returns,
        parameter_sets,
        pass_seeds,
        shuffle_method,
        block_size=None,
    ):
        self.calls.append(
            {
                "strategy_name": strategy.name,
                "backtest_fn_path": strategy.backtest_fn_path,
                "n_parameter_sets": len(parameter_sets),
                "n_pass_seeds": len(pass_seeds),
                "shuffle_method": shuffle_method,
                "block_size": block_size,
            }
        )
        matrix = np.empty((len(pass_seeds), len(parameter_sets)), dtype=np.float64)
        for row, seed in enumerate(pass_seeds):
            data = returns if row == 0 else np.random.default_rng(seed).permutation(returns)
            for col, parameter_set in enumerate(parameter_sets):
                matrix[row, col] = self.evaluator(parameter_set.to_dict(), data)
        return matrix


class _FailingRunner:
    """PassRunner fake that always blows up."""

    def run_passes(self, *args, **kwargs):
        raise RuntimeError("worker pool exploded")


class _RecordingRenderer:
    """ChartRenderer fake that records dashboard calls."""

    def __init__(self):
        self.dashboard_calls: list[dict] = []

    def plot_null_distribution(self, results, save_path=None):
        return "null"

    def plot_parameter_sensitivity(self, results, save_path=None, show_individual=True):
        return "sensitivity"

    def plot_surface_2d(self, results, param_x, param_y, save_path=None):
        return "surface"

    def plot_overfit_dashboard(
        self,
        results,
        pbo_results=None,
        wf_results=None,
        ac_diagnostic=None,
        n_obs=None,
        save_path=None,
    ):
        self.dashboard_calls.append(
            {
                "results": results,
                "pbo_results": pbo_results,
                "wf_results": wf_results,
                "save_path": save_path,
            }
        )
        return "dashboard"


@pytest.fixture
def returns_series():
    rng = np.random.default_rng(7)
    return rng.normal(0.0004, 0.01, size=400)


@pytest.fixture
def grid():
    return {"lookback": [3, 6], "threshold": [0.0, 0.001]}


def _make_analyzer(returns, grid, runner=None, renderer=None, **overrides):
    runner = _EvaluatingRunner() if runner is None else runner
    kwargs = {"n_reps": 12, "seed": 11, "strategy_name": "momo"}
    kwargs.update(overrides)
    analyzer = SignalAnalyzer(
        runner=runner,
        backtest_fn=momentum_backtest,
        returns=returns,
        signal_params=grid,
        chart_renderer=renderer,
        **kwargs,
    )
    return analyzer, runner


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestInit:
    def test_grid_metadata(self, returns_series, grid):
        analyzer, _ = _make_analyzer(list(returns_series), grid)
        assert analyzer.signal_names == ["lookback", "threshold"]
        assert analyzer.n_signals == 2
        assert analyzer.total_combinations == 4
        assert isinstance(analyzer.returns, np.ndarray)
        assert analyzer.returns.dtype == np.float64
        assert {"lookback": 3, "threshold": 0.0} in analyzer._param_list
        assert analyzer._overfit_results is None

    def test_empty_grid_rejected(self, returns_series):
        with pytest.raises(ValidationError):
            _make_analyzer(returns_series, {})


# ---------------------------------------------------------------------------
# run_detector
# ---------------------------------------------------------------------------


class TestRunDetector:
    def test_returns_results_and_caches(self, returns_series, grid):
        analyzer, runner = _make_analyzer(returns_series, grid)
        results = analyzer.run_detector()
        assert isinstance(results, OverfitResults)
        assert results is analyzer._overfit_results
        assert results.n_params == 4
        assert results.n_reps == 12
        assert len(results.original_sharpes) == 4

        assert len(runner.calls) == 1
        call = runner.calls[0]
        assert call["strategy_name"] == "momo"
        assert call["backtest_fn_path"].endswith("momentum_backtest")
        assert call["n_parameter_sets"] == 4
        assert call["n_pass_seeds"] == 13  # n_reps + 1 unpermuted pass
        assert call["shuffle_method"] == "complete"

    def test_n_reps_override(self, returns_series, grid):
        analyzer, _ = _make_analyzer(returns_series, grid)
        results = analyzer.run_detector(n_reps=5)
        assert results.n_reps == 5
        assert len(results.null_best_sharpes) == 5

    def test_invalid_shuffle_method(self, returns_series, grid):
        analyzer, _ = _make_analyzer(returns_series, grid, shuffle_method="bogus")
        with pytest.raises(ValidationError):
            analyzer.run_detector()

    def test_runner_failure_wrapped(self, returns_series, grid):
        analyzer, _ = _make_analyzer(returns_series, grid, runner=_FailingRunner())
        with pytest.raises(ValidationError, match="pass runner failed"):
            analyzer.run_detector()

    def test_nonfinite_returns_rejected(self, grid):
        bad = np.array([0.01, np.nan, 0.02])
        analyzer, _ = _make_analyzer(bad, grid)
        with pytest.raises(ValidationError):
            analyzer.run_detector()


# ---------------------------------------------------------------------------
# Statistics stages
# ---------------------------------------------------------------------------


class TestStages:
    def test_psr_dsr_lazily_runs_detector(self, returns_series, grid):
        analyzer, runner = _make_analyzer(returns_series, grid)
        assert analyzer._overfit_results is None
        psr, dsr = analyzer.compute_psr_dsr()
        assert analyzer._overfit_results is not None
        assert len(runner.calls) == 1
        assert isinstance(psr, PSRResult)
        assert isinstance(dsr, DSRResult)
        assert 0.0 <= psr.psr <= 1.0
        assert 0.0 <= dsr.dsr <= 1.0
        assert dsr.n_trials == 4
        assert dsr.sr_hat == pytest.approx(analyzer._overfit_results.best_sharpe)

    def test_psr_dsr_reuses_cached_detector(self, returns_series, grid):
        analyzer, runner = _make_analyzer(returns_series, grid)
        analyzer.run_detector()
        analyzer.compute_psr_dsr()
        assert len(runner.calls) == 1

    def test_compute_pbo(self, returns_series, grid):
        analyzer, _ = _make_analyzer(returns_series, grid)
        result = analyzer.compute_pbo(n_splits=4)
        assert isinstance(result, PBOResults)
        assert result is analyzer._pbo_result
        assert 0.0 <= result.pbo <= 1.0
        assert result.n_configs == 4
        assert result.n_splits == 4
        assert np.isfinite(result.logits).all()

    def test_compute_pbo_zero_sharpe_branch(self, returns_series):
        runner = _EvaluatingRunner(evaluator=zero_backtest)
        analyzer = SignalAnalyzer(
            runner=runner,
            backtest_fn=zero_backtest,
            returns=returns_series,
            signal_params={"alpha": [1, 2]},
            n_reps=4,
            seed=3,
        )
        result = analyzer.compute_pbo(n_splits=4)
        assert isinstance(result, PBOResults)
        assert np.isfinite(result.pbo)

    def test_compute_walkforward(self, returns_series, grid):
        analyzer, runner = _make_analyzer(returns_series, grid)
        result = analyzer.compute_walkforward(n_folds=4, purge_gap=3)
        assert isinstance(result, WalkForwardResults)
        assert result is analyzer._wf_result
        assert result.n_folds == 4
        assert result.purge_gap == 3
        assert len(result.is_sharpes) == 4
        assert np.isfinite(result.wfe)
        # Walk-forward does not need the permutation detector
        assert len(runner.calls) == 0

    def test_run_trial_tracker(self, returns_series, grid):
        analyzer, _ = _make_analyzer(returns_series, grid)
        tracker = analyzer.run_trial_tracker()
        assert isinstance(tracker, TrialTracker)
        assert tracker is analyzer._tracker
        assert tracker.n_trials == 4
        recorded = [trial.sharpe for trial in tracker.trials]
        expected = [float(s) for s in analyzer._overfit_results.original_sharpes]
        assert recorded == pytest.approx(expected)
        assert all(trial.strategy_name == "momo" for trial in tracker.trials)
        assert tracker.trials[0].params == analyzer._param_list[0]


# ---------------------------------------------------------------------------
# visualize
# ---------------------------------------------------------------------------


class TestVisualize:
    def test_no_renderer_returns_empty_without_running(self, returns_series, grid):
        analyzer, runner = _make_analyzer(returns_series, grid)
        assert analyzer.visualize() == []
        assert analyzer._overfit_results is None
        assert len(runner.calls) == 0

    def test_renderer_lazily_runs_detector(self, returns_series, grid):
        renderer = _RecordingRenderer()
        analyzer, _ = _make_analyzer(returns_series, grid, renderer=renderer)
        figures = analyzer.visualize(save_path="out.png")
        assert figures == ["dashboard"]
        assert analyzer._overfit_results is not None
        call = renderer.dashboard_calls[0]
        assert call["results"] is analyzer._overfit_results
        assert call["pbo_results"] is None
        assert call["wf_results"] is None
        assert call["save_path"] == "out.png"


# ---------------------------------------------------------------------------
# analyze (full pipeline)
# ---------------------------------------------------------------------------


class TestAnalyze:
    def test_full_pipeline(self, returns_series, grid):
        analyzer, runner = _make_analyzer(returns_series, grid)
        report = analyzer.analyze(n_reps=8, visualize=False)

        assert isinstance(report, SignalAnalysisReport)
        assert report.strategy_name == "momo"
        assert report.n_signals == 2
        assert report.signal_names == ["lookback", "threshold"]
        assert report.total_combinations == 4
        assert report.overfit_results is analyzer._overfit_results
        assert report.overfit_results.n_reps == 8
        assert report.psr_result is not None
        assert report.dsr_result is not None
        assert report.pbo_result is not None
        assert report.wf_result is not None
        assert report.trial_tracker.n_trials == 4
        assert report.figures == []
        assert report.verdict in {
            "GENUINE SIGNAL - proceed with caution",
            "MARGINAL - needs more data or fewer parameters",
            "LIKELY OVERFIT - high risk of curve fitting",
            "OVERFIT - almost certainly noise mining",
        }
        assert 0.0 <= report.confidence <= 1.0
        assert len(runner.calls) == 1

        lines = report.summary()
        assert "SIGNAL ANALYSIS: momo" in lines[1]
        assert any(line.startswith("VERDICT:") for line in lines)
        artifact = report.artifact_summary()
        assert artifact["figure_count"] == 0
        assert artifact["report_lines"] == lines

    def test_pbo_skipped_below_four_combinations(self, returns_series):
        analyzer, _ = _make_analyzer(returns_series, {"lookback": [3, 6], "threshold": [0.0]})
        assert analyzer.total_combinations == 2
        report = analyzer.analyze(n_reps=6, visualize=False)
        assert report.pbo_result is None
        assert report.wf_result is not None

    def test_optional_stages_disabled(self, returns_series, grid):
        analyzer, _ = _make_analyzer(returns_series, grid)
        report = analyzer.analyze(
            n_reps=6,
            run_pbo=False,
            run_walkforward=False,
            run_tracker=False,
            visualize=False,
        )
        assert report.pbo_result is None
        assert report.wf_result is None
        assert report.trial_tracker is None
        assert report.overfit_results is not None
        assert report.psr_result is not None
        assert report.figures == []

    def test_visualize_passes_stage_results_to_renderer(self, returns_series, grid):
        renderer = _RecordingRenderer()
        analyzer, _ = _make_analyzer(returns_series, grid, renderer=renderer)
        report = analyzer.analyze(n_reps=6, visualize=True, save_path="dash.png")
        assert report.figures == ["dashboard"]
        call = renderer.dashboard_calls[-1]
        assert call["pbo_results"] is report.pbo_result
        assert call["wf_results"] is report.wf_result
        assert call["save_path"] == "dash.png"


# ---------------------------------------------------------------------------
# Verdict synthesis
# ---------------------------------------------------------------------------


class _OverfitStub:
    def __init__(self, unbiased_pvalue, deflated_sharpe, plateau_score):
        self.unbiased_pvalue = unbiased_pvalue
        self.deflated_sharpe = deflated_sharpe
        self._plateau_score = plateau_score

    def surface_analysis(self):
        return {"plateau_score": self._plateau_score}


class _DSRStub:
    def __init__(self, is_significant):
        self.is_significant = is_significant


class _PBOStub:
    def __init__(self, pbo):
        self.pbo = pbo


class _WFStub:
    def __init__(self, wfe, vetoed=False):
        self.wfe = wfe
        self.vetoed = vetoed


def _bare_analyzer():
    analyzer = SignalAnalyzer(
        runner=_FailingRunner(),
        backtest_fn=momentum_backtest,
        returns=np.zeros(16),
        signal_params={"alpha": [1, 2]},
    )
    return analyzer


class TestSynthesizeVerdict:
    def test_no_results_is_insufficient_data(self):
        analyzer = _bare_analyzer()
        verdict, confidence = analyzer._synthesize_verdict()
        assert verdict == "INSUFFICIENT DATA"
        assert confidence == 0.0

    def test_all_positive_votes(self):
        analyzer = _bare_analyzer()
        analyzer._overfit_results = _OverfitStub(0.01, 3.0, 0.5)
        analyzer._dsr_result = _DSRStub(is_significant=True)
        analyzer._pbo_result = _PBOStub(0.05)
        analyzer._wf_result = _WFStub(0.8)
        verdict, confidence = analyzer._synthesize_verdict()
        assert verdict == "GENUINE SIGNAL - proceed with caution"
        assert confidence == pytest.approx(1.0)

    def test_all_negative_votes_with_veto(self):
        analyzer = _bare_analyzer()
        analyzer._overfit_results = _OverfitStub(0.5, 0.5, 0.01)
        analyzer._dsr_result = _DSRStub(is_significant=False)
        analyzer._pbo_result = _PBOStub(0.7)
        analyzer._wf_result = _WFStub(0.1, vetoed=True)
        verdict, confidence = analyzer._synthesize_verdict()
        assert verdict == "OVERFIT - almost certainly noise mining"
        assert confidence == pytest.approx(0.0)

    def test_marginal_mid_branches(self):
        analyzer = _bare_analyzer()
        # (0.6, 2) + (0.5, 1) + (0.5, 1) => 2.2 / 4 = 0.55
        analyzer._overfit_results = _OverfitStub(0.07, 1.5, 0.1)
        verdict, confidence = analyzer._synthesize_verdict()
        assert verdict == "MARGINAL - needs more data or fewer parameters"
        assert confidence == pytest.approx(0.55)

    def test_likely_overfit_band(self):
        analyzer = _bare_analyzer()
        # (0.6, 2) + (0.0, 1) + (0.0, 1) => 1.2 / 4 = 0.3
        analyzer._overfit_results = _OverfitStub(0.07, 0.5, 0.01)
        verdict, confidence = analyzer._synthesize_verdict()
        assert verdict == "LIKELY OVERFIT - high risk of curve fitting"
        assert confidence == pytest.approx(0.3)

    @pytest.mark.parametrize(
        ("pbo", "expected_score", "expected_prefix"),
        [
            (0.05, 1.0, "GENUINE SIGNAL"),
            (0.2, 0.6, "MARGINAL"),
            (0.4, 0.3, "LIKELY OVERFIT"),
            (0.7, 0.0, "OVERFIT - almost"),
        ],
    )
    def test_pbo_only_bands(self, pbo, expected_score, expected_prefix):
        analyzer = _bare_analyzer()
        analyzer._pbo_result = _PBOStub(pbo)
        verdict, confidence = analyzer._synthesize_verdict()
        assert verdict.startswith(expected_prefix)
        assert confidence == pytest.approx(expected_score)

    @pytest.mark.parametrize(
        ("wfe", "vetoed", "expected_score", "expected_prefix"),
        [
            (0.75, False, 1.0, "GENUINE SIGNAL"),
            (0.55, False, 0.6, "MARGINAL"),
            (0.35, False, 0.3, "LIKELY OVERFIT"),
            (0.1, False, 0.0, "OVERFIT - almost"),
            # Veto adds a heavy zero vote: (1*2 + 0*3) / 5 = 0.4
            (0.75, True, 0.4, "LIKELY OVERFIT"),
        ],
    )
    def test_walkforward_only_bands(self, wfe, vetoed, expected_score, expected_prefix):
        analyzer = _bare_analyzer()
        analyzer._wf_result = _WFStub(wfe, vetoed=vetoed)
        verdict, confidence = analyzer._synthesize_verdict()
        assert verdict.startswith(expected_prefix)
        assert confidence == pytest.approx(expected_score)
