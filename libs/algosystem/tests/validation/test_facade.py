"""Tests for the validation composition facade."""

import operator

import numpy as np
import pytest
from algosystem.shared.errors import ValidationError
from algosystem.validation.domain.results import OverfitResults
from algosystem.validation.domain.strategy import ParameterGrid, StrategySpec
from algosystem.validation.facade import OverfitDetector, detect_overfitting, resolve_strategy
from algosystem.validation.infrastructure.strategies import BACKTEST_FNS, STRATEGY_REGISTRY

GRID = {"window": [3, 5], "scale": [1.0, 2.0]}


def order_sensitive_backtest(params, returns):
    """Score that depends on data order so permutations change it."""
    window = int(params["window"])
    scale = float(params["scale"])
    return float(np.mean(returns[:window]) * scale)


def _returns(n=40):
    return np.linspace(-0.01, 0.02, n)


def _detector(**overrides):
    kwargs = {
        "backtest_fn": order_sensitive_backtest,
        "returns": _returns(),
        "param_grid": GRID,
        "n_reps": 3,
        "n_workers": 1,
        "seed": 7,
    }
    kwargs.update(overrides)
    return OverfitDetector(**kwargs)


def test_overfit_detector_builds_spec_and_coerces_returns():
    detector = _detector(returns=list(_returns()))

    assert isinstance(detector.returns, np.ndarray)
    assert detector.strategy.name == "order_sensitive_backtest"
    assert detector.strategy.parameter_grid.size == 4


def test_overfit_detector_param_list_is_name_sorted_cartesian_product():
    assert _detector().param_list == [
        {"scale": 1.0, "window": 3},
        {"scale": 1.0, "window": 5},
        {"scale": 2.0, "window": 3},
        {"scale": 2.0, "window": 5},
    ]


def test_overfit_detector_run_returns_consistent_results():
    detector = _detector()

    results = detector.run()

    assert isinstance(results, OverfitResults)
    assert results.n_params == 4
    assert results.n_reps == 3
    assert results.param_list == detector.param_list
    assert results.original_sharpes.shape == (4,)
    assert results.best_sharpe == pytest.approx(float(np.max(results.original_sharpes)))
    assert len(results.null_best_sharpes) == 3
    assert 0.0 <= results.prob_overfit <= 1.0


def test_overfit_detector_run_is_deterministic_for_a_seed():
    first = _detector().run()
    second = _detector().run()

    np.testing.assert_allclose(first.original_sharpes, second.original_sharpes)
    assert first.prob_overfit == second.prob_overfit


def test_overfit_detector_rejects_degenerate_returns():
    with pytest.raises(ValidationError):
        _detector(returns=[0.01])

    with pytest.raises(ValidationError):
        _detector(returns=[0.01, np.nan, 0.02])


def test_resolve_strategy_shipped_name_uses_registry():
    spec, evaluator = resolve_strategy("momentum")

    assert spec is STRATEGY_REGISTRY["momentum"]
    assert evaluator is BACKTEST_FNS["momentum"]


def test_resolve_strategy_shipped_name_with_grid_override():
    spec, evaluator = resolve_strategy("momentum", {"lookback": [3, 4]})

    assert spec is not STRATEGY_REGISTRY["momentum"]
    assert spec.name == "momentum"
    assert spec.backtest_fn_path == STRATEGY_REGISTRY["momentum"].backtest_fn_path
    assert spec.parameter_grid.to_dict() == {"lookback": [3, 4]}
    assert evaluator is BACKTEST_FNS["momentum"]


def test_resolve_strategy_alias_maps_to_canonical():
    spec, evaluator = resolve_strategy("vol_regime")

    assert spec is STRATEGY_REGISTRY["volatility"]
    assert evaluator is BACKTEST_FNS["volatility"]


def test_resolve_strategy_blank_name_raises():
    with pytest.raises(ValidationError, match="strategy must not be empty"):
        resolve_strategy("   ")


def test_resolve_strategy_dotted_path_loads_callable():
    spec, evaluator = resolve_strategy("operator.add", {"x": [1, 2]})

    assert evaluator is operator.add
    assert spec.name == "operator.add"
    assert spec.parameter_grid.to_dict() == {"x": [1, 2]}


def test_resolve_strategy_colon_path_loads_callable():
    spec, evaluator = resolve_strategy("operator:add", {"x": [1]})

    assert evaluator is operator.add
    assert spec.name == "operator:add"


def test_resolve_strategy_path_requires_param_grid():
    with pytest.raises(ValidationError, match="param_grid is required"):
        resolve_strategy("operator.add")


def test_resolve_strategy_bare_unknown_name_raises():
    with pytest.raises(ValidationError, match="shipped name or qualified"):
        resolve_strategy("no_such_shipped_strategy", {"x": [1]})


def test_resolve_strategy_unresolvable_path_raises():
    with pytest.raises(ValidationError, match="could not resolve strategy callable"):
        resolve_strategy("definitely_not_a_module_xyz.backtest", {"x": [1]})


def test_resolve_strategy_non_callable_target_raises():
    with pytest.raises(ValidationError, match="not callable"):
        resolve_strategy("math.pi", {"x": [1]})


def test_resolve_strategy_spec_passthrough_and_grid_replacement():
    original = StrategySpec(
        name="ops",
        backtest_fn_path="operator.add",
        parameter_grid=ParameterGrid({"x": [1]}),
    )

    same_spec, evaluator = resolve_strategy(original)
    assert same_spec is original
    assert evaluator is operator.add

    replaced, _ = resolve_strategy(original, {"y": [1, 2, 3]})
    assert replaced is not original
    assert replaced.name == "ops"
    assert replaced.backtest_fn_path == "operator.add"
    assert replaced.parameter_grid.to_dict() == {"y": [1, 2, 3]}


def test_resolve_strategy_callable_requires_param_grid():
    with pytest.raises(ValidationError, match="param_grid is required"):
        resolve_strategy(order_sensitive_backtest)


def test_resolve_strategy_callable_with_grid():
    spec, evaluator = resolve_strategy(order_sensitive_backtest, GRID)

    assert evaluator is order_sensitive_backtest
    assert spec.name == "order_sensitive_backtest"
    assert spec.parameter_grid.size == 4


def test_resolve_strategy_rejects_unsupported_types():
    with pytest.raises(ValidationError, match="shipped name, StrategySpec, or callable"):
        resolve_strategy(123)


def test_detect_overfitting_runs_with_callable_strategy():
    results = detect_overfitting(
        strategy=order_sensitive_backtest,
        returns=list(_returns()),
        param_grid=GRID,
        n_reps=4,
        n_workers=1,
        seed=11,
    )

    assert isinstance(results, OverfitResults)
    assert results.n_params == 4
    assert results.n_reps == 4
    assert results.shuffle_method == "complete"


def test_detect_overfitting_honors_max_param_trials():
    full_combos = [
        {"scale": 1.0, "window": 3},
        {"scale": 1.0, "window": 5},
        {"scale": 2.0, "window": 3},
        {"scale": 2.0, "window": 5},
    ]

    results = detect_overfitting(
        strategy=order_sensitive_backtest,
        returns=_returns(),
        param_grid=GRID,
        n_reps=2,
        max_param_trials=2,
        n_workers=1,
        seed=5,
    )

    assert results.n_params == 2
    assert all(params in full_combos for params in results.param_list)


def test_detect_overfitting_requires_grid_for_callable():
    with pytest.raises(ValidationError, match="param_grid is required"):
        detect_overfitting(
            strategy=order_sensitive_backtest,
            returns=_returns(),
            n_reps=2,
            n_workers=1,
        )
