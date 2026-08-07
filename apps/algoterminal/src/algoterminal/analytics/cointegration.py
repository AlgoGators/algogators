"""Engle-Granger cointegration test between two price series."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from statsmodels.tsa.stattools import coint


@dataclass
class CointegrationResult:
    sym_a: str
    sym_b: str
    t_statistic: float
    p_value: float
    critical_values: dict[str, float]

    def is_cointegrated(self, alpha: float = 0.05) -> bool:
        return self.p_value < alpha


def engle_granger_test(data: dict[str, pd.Series], sym_a: str, sym_b: str) -> CointegrationResult:
    a = data[sym_a].dropna()
    b = data[sym_b].dropna()
    aligned = pd.concat([a, b], axis=1, join="inner").dropna()
    aligned.columns = [sym_a, sym_b]

    t_stat, p_value, crit = coint(aligned[sym_a], aligned[sym_b])
    return CointegrationResult(
        sym_a=sym_a,
        sym_b=sym_b,
        t_statistic=float(t_stat),
        p_value=float(p_value),
        critical_values={"1%": crit[0], "5%": crit[1], "10%": crit[2]},
    )
