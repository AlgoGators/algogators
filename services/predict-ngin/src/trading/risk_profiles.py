"""
Predefined risk profiles for strategy execution.
"""

from dataclasses import dataclass, replace
from typing import Dict, List

from .risk import RiskLimits, list_risk_modules


@dataclass(frozen=True)
class RiskProfile:
    """Named risk profile with limits and module ordering."""

    name: str
    limits: RiskLimits
    modules: List[str]


RISK_PROFILES: Dict[str, RiskProfile] = {
    "default": RiskProfile(
        name="default",
        limits=RiskLimits(),
        modules=list_risk_modules(),
    ),
    "conservative": RiskProfile(
        name="conservative",
        limits=RiskLimits(
            max_position_size=200,
            min_position_size=50,
            max_total_positions=20,
            max_total_exposure=5000,
            max_exposure_pct_of_capital=0.50,
            max_single_position_pct=0.05,
            max_daily_drawdown_pct=0.02,
            max_total_drawdown_pct=0.10,
            drawdown_halt_pct=0.15,
            min_liquidity=1000,
            min_volume_24h=500,
            max_spread_pct=0.05,
            vol_scaling_enabled=True,
            target_portfolio_volatility=0.10,
        ),
        modules=list_risk_modules(),
    ),
    "aggressive": RiskProfile(
        name="aggressive",
        limits=RiskLimits(
            max_position_size=2000,
            max_total_positions=100,
            max_total_exposure=50000,
            max_exposure_pct_of_capital=0.90,
            max_daily_drawdown_pct=0.10,
            max_total_drawdown_pct=0.30,
            min_liquidity=100,
            vol_scaling_enabled=False,
        ),
        modules=list_risk_modules(),
    ),
}


def list_risk_profiles() -> List[str]:
    """Return available risk profile names."""
    return sorted(RISK_PROFILES.keys())


def get_risk_profile(name: str) -> RiskProfile:
    """Fetch a risk profile by name."""
    if name not in RISK_PROFILES:
        raise ValueError(f"Unknown risk profile: {name}")
    profile = RISK_PROFILES[name]
    return RiskProfile(
        name=profile.name,
        limits=replace(profile.limits),
        modules=list(profile.modules),
    )
