from algogators.research.backtest import (
    BacktestResult,
    BacktestStats,
    has_backtest_result,
    load_backtest_result,
    run_backtest,
    save_backtest_result,
)
from algogators.research.data_stage import load_quality_reports, pull_and_validate, save_quality_reports
from algogators.research.hypothesis import run_hypothesis_wizard
from algogators.research.methodology import load_strategy_module, scaffold_strategy
from algogators.research.models import Hypothesis
from algogators.research.storage import ResearchRecord, create_record, latest_record, list_slugs, list_versions
from algogators.research.writeup import generate_writeup

__all__ = [
    "BacktestResult",
    "BacktestStats",
    "has_backtest_result",
    "load_backtest_result",
    "run_backtest",
    "save_backtest_result",
    "pull_and_validate",
    "save_quality_reports",
    "load_quality_reports",
    "run_hypothesis_wizard",
    "load_strategy_module",
    "scaffold_strategy",
    "Hypothesis",
    "ResearchRecord",
    "create_record",
    "latest_record",
    "list_slugs",
    "list_versions",
    "generate_writeup",
]
