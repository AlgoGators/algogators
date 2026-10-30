"""Compatibility facade for the portfolio application/infrastructure layers."""

import logging

from research_api.application.portfolio.use_cases import (
    build_strategy_detail,
    build_strategy_summary,
)
from research_api.domain.portfolio import calculations as _calculations
from research_api.domain.portfolio.streams import (  # noqa: F401 - re-exported facade API
    PORTFOLIO_STREAMS,
    PRIMARY_STREAM,
)
from research_api.infrastructure.portfolio.repositories import PostgresPortfolioRepository

# Facade re-exports: tests and legacy callers reach these through this module.
# Plain assignments (not import aliases) so the linter cannot prune them.
_build_historical_data = _calculations.build_historical_data
_compute_return_stats = _calculations.compute_return_stats
_f = _calculations.float_or_default
_resolve_initial_equity = _calculations.resolve_initial_equity
_transform_executions = _calculations.transform_executions
_transform_finalized = _calculations.transform_finalized
_transform_positions = _calculations.transform_positions

logger = logging.getLogger(__name__)


def _repository():
    return PostgresPortfolioRepository()


def _has_portfolio_type(cursor):
    return _repository()._has_portfolio_type(cursor)


# Issue #59: the strategy-type readers this facade once wrapped are gone; a book is read by
# its portfolio id only.


def get_strategy_detail(cfg):
    rows = _repository().fetch_portfolio_rows(cfg["portfolio_id"])
    detail = build_strategy_detail(cfg, rows)
    if detail is None:
        logger.warning("[PORTFOLIO] No live_results for %s", cfg["portfolio_id"])
    return detail


def get_strategy_summary(cfg):
    latest = _repository().fetch_summary_row(cfg["portfolio_id"])
    summary = build_strategy_summary(cfg, latest)
    if summary is None:
        logger.warning("[PORTFOLIO] No summary live_results for %s", cfg["portfolio_id"])
    return summary
