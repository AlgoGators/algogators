"""Performance metrics value object."""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real

from algosystem.shared.errors import CalculationError
from algosystem.shared.metric_key import LEGACY_ALIASES, MetricKey

MetricLookup = MetricKey | str


def _resolve_key(key: MetricLookup) -> MetricKey:
    if isinstance(key, MetricKey):
        return key
    if isinstance(key, str):
        if key in LEGACY_ALIASES:
            return LEGACY_ALIASES[key]
        try:
            return MetricKey(key)
        except ValueError as exc:
            raise CalculationError(f"unknown metric key: {key}") from exc
    raise CalculationError("metric key must be a MetricKey or string")


def _valid_metric_names() -> str:
    valid_names = sorted({metric_key.value for metric_key in MetricKey} | set(LEGACY_ALIASES))
    return ", ".join(valid_names)


def _resolve_supported_lookup(key: MetricLookup) -> MetricKey:
    if isinstance(key, MetricKey):
        return key
    if isinstance(key, str):
        if key in LEGACY_ALIASES:
            return LEGACY_ALIASES[key]
        try:
            return MetricKey(key)
        except ValueError as exc:
            raise KeyError(
                f"unknown metric key {key!r}; valid keys: {_valid_metric_names()}"
            ) from exc
    raise ValueError(
        f"metric key must be a MetricKey or string, got {type(key).__name__}; "
        f"valid keys: {_valid_metric_names()}"
    )


def _normalize_value(value: object, key: MetricKey) -> float | None:
    if value is None:
        return None
    if not isinstance(value, Real) or isinstance(value, bool):
        raise CalculationError(f"metric {key.value} must be numeric or None")
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


@dataclass(frozen=True)
class PerformanceMetrics:
    """Static performance metrics keyed by MetricKey."""

    total_return: float | None = None
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    max_drawdown: float | None = None
    downside_deviation: float | None = None
    var_95: float | None = None
    var_99: float | None = None
    cvar_95: float | None = None
    skewness: float | None = None
    kurtosis: float | None = None
    jarque_bera_stat: float | None = None
    jarque_bera_pvalue: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    positive_days: float | None = None
    negative_days: float | None = None
    pct_positive_days: float | None = None
    best_month: float | None = None
    worst_month: float | None = None
    avg_monthly_return: float | None = None
    monthly_volatility: float | None = None
    pct_positive_months: float | None = None
    alpha: float | None = None
    beta: float | None = None
    correlation: float | None = None
    tracking_error: float | None = None
    information_ratio: float | None = None
    capture_ratio_up: float | None = None
    capture_ratio_down: float | None = None

    def __post_init__(self) -> None:
        for key in MetricKey:
            value = getattr(self, key.value)
            object.__setattr__(self, key.value, _normalize_value(value, key))

    def get(self, key: MetricLookup) -> float | None:
        """Return a metric value by canonical key."""
        metric_key = _resolve_supported_lookup(key)
        return getattr(self, metric_key.value)

    def to_dict(self) -> dict[str, float]:
        """Return non-empty metrics keyed by MetricKey.value."""
        metrics: dict[str, float] = {}
        for key in MetricKey:
            value = self.get(key)
            if value is not None:
                metrics[key.value] = value
        return metrics

    @classmethod
    def from_dict(cls, mapping: Mapping[MetricLookup, object]) -> PerformanceMetrics:
        """Build metrics from MetricKey or string keys."""
        values: dict[str, float | None] = {}
        for raw_key, raw_value in mapping.items():
            key = _resolve_key(raw_key)
            values[key.value] = _normalize_value(raw_value, key)
        return cls(**values)

    def benchmark_relative(self) -> dict[MetricKey, float | None]:
        """Return benchmark-dependent metrics only."""
        return {key: self.get(key) for key in MetricKey if key.is_benchmark_relative()}

    def __getitem__(self, key: str) -> float | None:
        warnings.warn(
            "String metric lookup is deprecated; use MetricKey and PerformanceMetrics.get().",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.get(_resolve_key(key))

    def __contains__(self, key: object) -> bool:
        warnings.warn(
            "String metric membership is deprecated; use MetricKey and PerformanceMetrics.get().",
            DeprecationWarning,
            stacklevel=2,
        )
        try:
            metric_key = _resolve_key(key)  # type: ignore[arg-type]
        except CalculationError:
            return False
        return self.get(metric_key) is not None
