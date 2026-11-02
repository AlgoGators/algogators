"""
Automatic Parameter Tuning

Provides grid search, random search, and optimization-based parameter tuning
for trading strategies.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Callable, Tuple
from itertools import product
import random
import numpy as np
import pandas as pd
from scipy.optimize import minimize, differential_evolution
import json
from pathlib import Path


@dataclass
class ParameterSpace:
    """Define a parameter space for tuning."""
    
    name: str
    param_type: str  # "continuous", "discrete", "categorical"
    bounds: Optional[Tuple[float, float]] = None  # For continuous/discrete
    values: Optional[List[Any]] = None  # For categorical/discrete
    log_scale: bool = False  # For continuous parameters


@dataclass
class TuningResult:
    """Result from parameter tuning."""
    
    best_params: Dict[str, Any]
    best_score: float
    all_results: List[Dict[str, Any]]
    optimization_method: str
    n_iterations: int


class ParameterTuner:
    """Automatic parameter tuning for trading strategies."""
    
    def __init__(
        self,
        objective_function: Callable[[Dict[str, Any]], float],
        parameter_spaces: Dict[str, ParameterSpace],
        maximize: bool = True,
    ):
        """
        Initialize parameter tuner.
        
        Args:
            objective_function: Function that takes params dict and returns score
            parameter_spaces: Dict mapping parameter names to ParameterSpace
            maximize: If True, maximize objective; if False, minimize
        """
        self.objective_function = objective_function
        self.parameter_spaces = parameter_spaces
        self.maximize = maximize
        self._sign = 1 if maximize else -1
    
    def grid_search(
        self,
        max_combinations: Optional[int] = None,
        random_sample: bool = False,
    ) -> TuningResult:
        """
        Perform grid search over parameter space.
        
        Args:
            max_combinations: Maximum number of combinations to try
            random_sample: If True, randomly sample combinations instead of exhaustive
        
        Returns:
            TuningResult with best parameters
        """
        # Generate all parameter combinations
        param_lists = {}
        for name, space in self.parameter_spaces.items():
            if space.param_type == "continuous":
                # For continuous, use bounds with reasonable discretization
                if space.bounds:
                    if space.log_scale:
                        low, high = space.bounds
                        n_points = 10
                        param_lists[name] = np.logspace(
                            np.log10(low), np.log10(high), n_points
                        ).tolist()
                    else:
                        low, high = space.bounds
                        n_points = 10
                        param_lists[name] = np.linspace(low, high, n_points).tolist()
            elif space.param_type == "discrete":
                if space.bounds:
                    low, high = space.bounds
                    step = (high - low) / 10
                    param_lists[name] = np.arange(low, high + step, step).tolist()
                elif space.values:
                    param_lists[name] = space.values
            elif space.param_type == "categorical":
                param_lists[name] = space.values or []
        
        # Generate all combinations
        param_names = list(param_lists.keys())
        param_values = [param_lists[name] for name in param_names]
        
        all_combinations = list(product(*param_values))
        
        # Limit combinations if needed
        if max_combinations and len(all_combinations) > max_combinations:
            if random_sample:
                all_combinations = random.sample(all_combinations, max_combinations)
            else:
                all_combinations = all_combinations[:max_combinations]
        
        # Evaluate all combinations
        results = []
        best_score = float('-inf') if self.maximize else float('inf')
        best_params = None
        
        for i, combination in enumerate(all_combinations):
            params = dict(zip(param_names, combination))
            
            try:
                score = self.objective_function(params)
                results.append({
                    "params": params,
                    "score": score,
                    "iteration": i + 1,
                })
                
                if (self.maximize and score > best_score) or (not self.maximize and score < best_score):
                    best_score = score
                    best_params = params.copy()
            except Exception as e:
                print(f"Error evaluating params {params}: {e}")
                continue
        
        return TuningResult(
            best_params=best_params or {},
            best_score=best_score,
            all_results=results,
            optimization_method="grid_search",
            n_iterations=len(results),
        )
    
    def random_search(
        self,
        n_iterations: int = 100,
    ) -> TuningResult:
        """
        Perform random search over parameter space.
        
        Args:
            n_iterations: Number of random samples to try
        
        Returns:
            TuningResult with best parameters
        """
        results = []
        best_score = float('-inf') if self.maximize else float('inf')
        best_params = None
        
        for i in range(n_iterations):
            params = {}
            
            # Sample each parameter
            for name, space in self.parameter_spaces.items():
                if space.param_type == "continuous":
                    if space.bounds:
                        low, high = space.bounds
                        if space.log_scale:
                            params[name] = np.exp(np.random.uniform(
                                np.log(low), np.log(high)
                            ))
                        else:
                            params[name] = np.random.uniform(low, high)
                elif space.param_type == "discrete":
                    if space.bounds:
                        low, high = space.bounds
                        params[name] = np.random.randint(int(low), int(high) + 1)
                    elif space.values:
                        params[name] = random.choice(space.values)
                elif space.param_type == "categorical":
                    if space.values:
                        params[name] = random.choice(space.values)
            
            try:
                score = self.objective_function(params)
                results.append({
                    "params": params,
                    "score": score,
                    "iteration": i + 1,
                })
                
                if (self.maximize and score > best_score) or (not self.maximize and score < best_score):
                    best_score = score
                    best_params = params.copy()
            except Exception as e:
                print(f"Error evaluating params {params}: {e}")
                continue
        
        return TuningResult(
            best_params=best_params or {},
            best_score=best_score,
            all_results=results,
            optimization_method="random_search",
            n_iterations=len(results),
        )
    
    def bayesian_optimization(
        self,
        n_iterations: int = 50,
        n_initial: int = 10,
    ) -> TuningResult:
        """
        Perform Bayesian optimization (using scipy's differential evolution as proxy).
        
        Args:
            n_iterations: Number of optimization iterations
            n_initial: Number of initial random samples
        
        Returns:
            TuningResult with best parameters
        """
        # Get continuous parameters for optimization
        continuous_params = {
            name: space for name, space in self.parameter_spaces.items()
            if space.param_type == "continuous" and space.bounds
        }
        
        if not continuous_params:
            # Fall back to random search if no continuous params
            return self.random_search(n_iterations=n_iterations)
        
        param_names = list(continuous_params.keys())
        bounds = [continuous_params[name].bounds for name in param_names]
        
        # Store discrete/categorical params (sample randomly)
        fixed_params = {}
        for name, space in self.parameter_spaces.items():
            if name not in continuous_params:
                if space.param_type == "discrete" and space.values:
                    fixed_params[name] = random.choice(space.values)
                elif space.param_type == "categorical" and space.values:
                    fixed_params[name] = random.choice(space.values)
        
        def objective_vector(x):
            """Objective function for scipy optimizer."""
            params = dict(zip(param_names, x))
            params.update(fixed_params)
            try:
                score = self.objective_function(params)
                return -self._sign * score  # Negate for minimization
            except Exception as e:
                return float('inf') if self.maximize else float('-inf')
        
        # Run optimization
        result = differential_evolution(
            objective_vector,
            bounds,
            maxiter=n_iterations,
            popsize=15,
            seed=42,
        )
        
        best_params = dict(zip(param_names, result.x))
        best_params.update(fixed_params)
        best_score = -result.fun * self._sign
        
        # Collect all results (simplified - in practice would track all evaluations)
        all_results = [{
            "params": best_params,
            "score": best_score,
            "iteration": n_iterations,
        }]
        
        return TuningResult(
            best_params=best_params,
            best_score=best_score,
            all_results=all_results,
            optimization_method="bayesian_optimization",
            n_iterations=n_iterations,
        )
    
    def optimize(
        self,
        method: str = "random_search",
        **kwargs,
    ) -> TuningResult:
        """
        Run optimization with specified method.
        
        Args:
            method: "grid_search", "random_search", or "bayesian_optimization"
            **kwargs: Additional arguments for the optimization method
        
        Returns:
            TuningResult
        """
        if method == "grid_search":
            return self.grid_search(**kwargs)
        elif method == "random_search":
            return self.random_search(**kwargs)
        elif method == "bayesian_optimization":
            return self.bayesian_optimization(**kwargs)
        else:
            raise ValueError(f"Unknown optimization method: {method}")


def save_tuning_result(
    result: TuningResult,
    output_path: str,
) -> None:
    """Save tuning result to JSON file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    data = {
        "best_params": result.best_params,
        "best_score": result.best_score,
        "optimization_method": result.optimization_method,
        "n_iterations": result.n_iterations,
        "all_results": result.all_results[:100],  # Limit to first 100 for file size
    }
    
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_tuning_result(output_path: str) -> TuningResult:
    """Load tuning result from JSON file."""
    with open(output_path, "r") as f:
        data = json.load(f)
    
    return TuningResult(
        best_params=data["best_params"],
        best_score=data["best_score"],
        all_results=data.get("all_results", []),
        optimization_method=data["optimization_method"],
        n_iterations=data["n_iterations"],
    )


def tuning_result_to_dataframe(result: TuningResult) -> pd.DataFrame:
    """Convert tuning result to DataFrame."""
    if not result.all_results:
        return pd.DataFrame()
    
    rows = []
    for r in result.all_results:
        row = r["params"].copy()
        row["score"] = r["score"]
        row["iteration"] = r["iteration"]
        rows.append(row)
    
    return pd.DataFrame(rows).sort_values("score", ascending=not result.best_score > 0)
