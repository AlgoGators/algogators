"""
Tests for parameter tuning functionality.
"""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

# Add src to path for direct imports
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Import directly from module file to avoid package import issues
import importlib.util
parameter_tuning_path = SRC / "trading" / "parameter_tuning.py"
spec = importlib.util.spec_from_file_location("parameter_tuning", parameter_tuning_path)
parameter_tuning_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parameter_tuning_module)

# Import classes/functions from the module
ParameterSpace = parameter_tuning_module.ParameterSpace
ParameterTuner = parameter_tuning_module.ParameterTuner
TuningResult = parameter_tuning_module.TuningResult
save_tuning_result = parameter_tuning_module.save_tuning_result
load_tuning_result = parameter_tuning_module.load_tuning_result
tuning_result_to_dataframe = parameter_tuning_module.tuning_result_to_dataframe


@pytest.fixture
def simple_objective():
    """Simple objective function for testing."""
    def objective(params):
        # Simple quadratic function: maximize -(x-0.5)^2
        x = params.get("x", 0.5)
        return -(x - 0.5) ** 2
    return objective


@pytest.fixture
def multi_param_objective():
    """Multi-parameter objective function."""
    def objective(params):
        x = params.get("x", 0.0)
        y = params.get("y", 0.0)
        # Maximize: -(x^2 + y^2) (best at x=0, y=0)
        return -(x ** 2 + y ** 2)
    return objective


@pytest.fixture
def parameter_spaces_simple():
    """Simple parameter space for testing."""
    return {
        "x": ParameterSpace(
            name="x",
            param_type="continuous",
            bounds=(0.0, 1.0),
        ),
    }


@pytest.fixture
def parameter_spaces_multi():
    """Multi-parameter space for testing."""
    return {
        "x": ParameterSpace(
            name="x",
            param_type="continuous",
            bounds=(-1.0, 1.0),
        ),
        "y": ParameterSpace(
            name="y",
            param_type="continuous",
            bounds=(-1.0, 1.0),
        ),
    }


@pytest.fixture
def parameter_spaces_mixed():
    """Mixed parameter types for testing."""
    return {
        "continuous_param": ParameterSpace(
            name="continuous_param",
            param_type="continuous",
            bounds=(0.0, 10.0),
        ),
        "discrete_param": ParameterSpace(
            name="discrete_param",
            param_type="discrete",
            bounds=(1, 10),
        ),
        "categorical_param": ParameterSpace(
            name="categorical_param",
            param_type="categorical",
            values=["option_a", "option_b", "option_c"],
        ),
    }


def test_parameter_space_creation():
    """Test ParameterSpace creation."""
    # Continuous
    space = ParameterSpace(
        name="test",
        param_type="continuous",
        bounds=(0.0, 1.0),
    )
    assert space.name == "test"
    assert space.param_type == "continuous"
    assert space.bounds == (0.0, 1.0)
    
    # Categorical
    space = ParameterSpace(
        name="test",
        param_type="categorical",
        values=["a", "b", "c"],
    )
    assert space.values == ["a", "b", "c"]


def test_parameter_tuner_initialization(simple_objective, parameter_spaces_simple):
    """Test ParameterTuner initialization."""
    tuner = ParameterTuner(
        objective_function=simple_objective,
        parameter_spaces=parameter_spaces_simple,
        maximize=True,
    )
    
    assert tuner.maximize is True
    assert tuner._sign == 1


def test_grid_search_simple(simple_objective, parameter_spaces_simple, tmp_path):
    """Test grid search with simple parameter space."""
    tuner = ParameterTuner(
        objective_function=simple_objective,
        parameter_spaces=parameter_spaces_simple,
        maximize=True,
    )
    
    result = tuner.grid_search(max_combinations=20)
    
    assert isinstance(result, TuningResult)
    assert result.optimization_method == "grid_search"
    assert result.best_params is not None
    assert "x" in result.best_params
    assert len(result.all_results) > 0
    
    # Best x should be close to 0.5 (optimal)
    assert abs(result.best_params["x"] - 0.5) < 0.2


def test_grid_search_max_combinations(simple_objective, parameter_spaces_simple):
    """Test grid search respects max_combinations limit."""
    tuner = ParameterTuner(
        objective_function=simple_objective,
        parameter_spaces=parameter_spaces_simple,
        maximize=True,
    )
    
    result = tuner.grid_search(max_combinations=5)
    
    assert len(result.all_results) <= 5


def test_random_search(simple_objective, parameter_spaces_simple):
    """Test random search."""
    tuner = ParameterTuner(
        objective_function=simple_objective,
        parameter_spaces=parameter_spaces_simple,
        maximize=True,
    )
    
    result = tuner.random_search(n_iterations=20)
    
    assert isinstance(result, TuningResult)
    assert result.optimization_method == "random_search"
    assert result.n_iterations == 20
    assert result.best_params is not None
    assert len(result.all_results) == 20


def test_random_search_multi_param(multi_param_objective, parameter_spaces_multi):
    """Test random search with multiple parameters."""
    tuner = ParameterTuner(
        objective_function=multi_param_objective,
        parameter_spaces=parameter_spaces_multi,
        maximize=True,
    )
    
    result = tuner.random_search(n_iterations=30)
    
    assert "x" in result.best_params
    assert "y" in result.best_params
    assert len(result.all_results) == 30


def test_bayesian_optimization(simple_objective, parameter_spaces_simple):
    """Test Bayesian optimization."""
    tuner = ParameterTuner(
        objective_function=simple_objective,
        parameter_spaces=parameter_spaces_simple,
        maximize=True,
    )
    
    result = tuner.bayesian_optimization(n_iterations=10)
    
    assert isinstance(result, TuningResult)
    assert result.optimization_method == "bayesian_optimization"
    assert result.best_params is not None


def test_bayesian_optimization_mixed_types(multi_param_objective, parameter_spaces_mixed):
    """Test Bayesian optimization falls back for non-continuous params."""
    tuner = ParameterTuner(
        objective_function=multi_param_objective,
        parameter_spaces=parameter_spaces_mixed,
        maximize=True,
    )
    
    # Should fall back to random search if no continuous params
    result = tuner.bayesian_optimization(n_iterations=10)
    assert result.optimization_method == "bayesian_optimization"


def test_optimize_method(simple_objective, parameter_spaces_simple):
    """Test optimize method dispatcher."""
    tuner = ParameterTuner(
        objective_function=simple_objective,
        parameter_spaces=parameter_spaces_simple,
        maximize=True,
    )
    
    # Grid search
    result = tuner.optimize(method="grid_search", max_combinations=10)
    assert result.optimization_method == "grid_search"
    
    # Random search
    result = tuner.optimize(method="random_search", n_iterations=10)
    assert result.optimization_method == "random_search"
    
    # Bayesian
    result = tuner.optimize(method="bayesian_optimization", n_iterations=5)
    assert result.optimization_method == "bayesian_optimization"
    
    # Invalid method
    with pytest.raises(ValueError):
        tuner.optimize(method="invalid_method")


def test_minimize_objective():
    """Test minimization objective."""
    def minimize_objective(params):
        x = params.get("x", 0.5)
        return (x - 0.5) ** 2  # Minimize (best at x=0.5)
    
    spaces = {
        "x": ParameterSpace(
            name="x",
            param_type="continuous",
            bounds=(0.0, 1.0),
        ),
    }
    
    tuner = ParameterTuner(
        objective_function=minimize_objective,
        parameter_spaces=spaces,
        maximize=False,
    )
    
    result = tuner.random_search(n_iterations=20)
    
    assert tuner.maximize is False
    assert tuner._sign == -1
    # Best x should be close to 0.5
    assert abs(result.best_params["x"] - 0.5) < 0.3


def test_save_and_load_tuning_result(simple_objective, parameter_spaces_simple, tmp_path):
    """Test saving and loading tuning results."""
    tuner = ParameterTuner(
        objective_function=simple_objective,
        parameter_spaces=parameter_spaces_simple,
        maximize=True,
    )
    
    result = tuner.random_search(n_iterations=10)
    
    # Save
    output_path = tmp_path / "tuning_result.json"
    save_tuning_result(result, str(output_path))
    
    assert output_path.exists()
    
    # Load
    loaded_result = load_tuning_result(str(output_path))
    
    assert loaded_result.best_params == result.best_params
    assert loaded_result.best_score == result.best_score
    assert loaded_result.optimization_method == result.optimization_method
    assert loaded_result.n_iterations == result.n_iterations


def test_tuning_result_to_dataframe(simple_objective, parameter_spaces_simple):
    """Test conversion to DataFrame."""
    import pandas as pd
    
    tuner = ParameterTuner(
        objective_function=simple_objective,
        parameter_spaces=parameter_spaces_simple,
        maximize=True,
    )
    
    result = tuner.random_search(n_iterations=10)
    
    df = tuning_result_to_dataframe(result)
    
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert "x" in df.columns
    assert "score" in df.columns
    assert "iteration" in df.columns


def test_error_handling_in_objective():
    """Test that errors in objective function are handled gracefully."""
    def failing_objective(params):
        if params.get("x", 0) > 0.5:
            raise ValueError("Test error")
        return params.get("x", 0)
    
    spaces = {
        "x": ParameterSpace(
            name="x",
            param_type="continuous",
            bounds=(0.0, 1.0),
        ),
    }
    
    tuner = ParameterTuner(
        objective_function=failing_objective,
        parameter_spaces=spaces,
        maximize=True,
    )
    
    # Should not raise, but skip failed evaluations
    result = tuner.random_search(n_iterations=10)
    
    # Should have some successful results
    assert len(result.all_results) > 0


def test_empty_parameter_spaces():
    """Test behavior with empty parameter spaces."""
    def objective(params):
        return 1.0
    
    tuner = ParameterTuner(
        objective_function=objective,
        parameter_spaces={},
        maximize=True,
    )
    
    result = tuner.random_search(n_iterations=5)
    
    assert result.best_params == {}
    assert len(result.all_results) == 5
