#!/usr/bin/env python3
"""
Parameter Tuning Script

Automatically tunes strategy parameters using grid search, random search, or optimization.
"""

import argparse
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
from typing import Dict, Any

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from src.trading.parameter_tuning import (
    ParameterTuner,
    ParameterSpace,
    save_tuning_result,
)
from src.config import get_config


def create_momentum_objective():
    """Create objective function for momentum strategy."""
    def objective(params: Dict[str, Any]) -> float:
        # This would run a backtest with the given parameters
        # For now, return a placeholder
        # In practice, you'd import and run the actual backtest here
        print(f"Evaluating params: {params}")
        # Placeholder - replace with actual backtest
        return 0.0
    return objective


def create_smart_money_objective():
    """Create objective function for smart money strategy."""
    def objective(params: Dict[str, Any]) -> float:
        print(f"Evaluating params: {params}")
        # Placeholder - replace with actual backtest
        return 0.0
    return objective


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Tune strategy parameters automatically"
    )
    parser.add_argument(
        "--strategy",
        type=str,
        required=True,
        choices=["momentum", "smart_money", "breakout", "mean_reversion"],
        help="Strategy to tune"
    )
    parser.add_argument(
        "--method",
        type=str,
        default="random_search",
        choices=["grid_search", "random_search", "bayesian_optimization"],
        help="Optimization method"
    )
    parser.add_argument(
        "--n-iterations",
        type=int,
        default=50,
        help="Number of iterations (for random_search/bayesian_optimization)"
    )
    parser.add_argument(
        "--max-combinations",
        type=int,
        default=100,
        help="Max combinations for grid_search"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/research/parameter_tuning.json",
        help="Output path for tuning results"
    )
    
    args = parser.parse_args()
    
    # Define parameter spaces based on strategy
    if args.strategy == "momentum":
        parameter_spaces = {
            "lookback_hours": ParameterSpace(
                name="lookback_hours",
                param_type="discrete",
                bounds=(12, 72),
            ),
            "min_return": ParameterSpace(
                name="min_return",
                param_type="continuous",
                bounds=(0.01, 0.10),
            ),
            "price_bounds_min": ParameterSpace(
                name="price_bounds_min",
                param_type="continuous",
                bounds=(0.05, 0.30),
            ),
            "price_bounds_max": ParameterSpace(
                name="price_bounds_max",
                param_type="continuous",
                bounds=(0.70, 0.95),
            ),
        }
        objective = create_momentum_objective()
    
    elif args.strategy == "smart_money":
        parameter_spaces = {
            "min_trade_size": ParameterSpace(
                name="min_trade_size",
                param_type="discrete",
                bounds=(500, 5000),
            ),
            "aggregation_window_hours": ParameterSpace(
                name="aggregation_window_hours",
                param_type="discrete",
                bounds=(2, 12),
            ),
            "imbalance_threshold": ParameterSpace(
                name="imbalance_threshold",
                param_type="continuous",
                bounds=(0.4, 0.8),
            ),
        }
        objective = create_smart_money_objective()
    
    else:
        print(f"Strategy {args.strategy} not yet implemented")
        return 1
    
    # Create tuner
    tuner = ParameterTuner(
        objective_function=objective,
        parameter_spaces=parameter_spaces,
        maximize=True,  # Maximize Sharpe ratio or return
    )
    
    # Run optimization
    print(f"Running {args.method} optimization for {args.strategy}...")
    
    if args.method == "grid_search":
        result = tuner.grid_search(max_combinations=args.max_combinations)
    elif args.method == "random_search":
        result = tuner.random_search(n_iterations=args.n_iterations)
    elif args.method == "bayesian_optimization":
        result = tuner.bayesian_optimization(n_iterations=args.n_iterations)
    
    # Save results
    save_tuning_result(result, args.output)
    
    print(f"\nOptimization complete!")
    print(f"Best parameters: {result.best_params}")
    print(f"Best score: {result.best_score:.4f}")
    print(f"Results saved to {args.output}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
