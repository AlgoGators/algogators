"""Pilot configuration and the three-gate live switch.

Live order entry requires ALL of:
  1. the CLI flag ``--live``;
  2. the environment variable ``LIP_PILOT_LIVE=I_UNDERSTAND``;
  3. the config file fields ``live: true`` and ``dry_run: false``.
Otherwise the pilot is a dry run that only logs the orders it would place.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from predict_ngin.venues.kalshi.endpoints import BASE_URL

DEFAULT_CONFIG_PATH = Path(__file__).with_name("pilot.yaml")
LIVE_ENV_VAR = "LIP_PILOT_LIVE"
LIVE_ENV_VALUE = "I_UNDERSTAND"

# Hard ceilings the config file cannot raise (the user's approval: <= $250 at risk).
HARD_MAX_TOTAL_AT_RISK_USD = 250.0
HARD_MAX_PER_MARKET_AT_RISK_USD = 50.0
HARD_MAX_MARKETS = 5


@dataclass
class ApiCfg:
    base_url: str = BASE_URL
    timeout_s: float = 15.0
    read_rate_per_s: float = 5.0
    write_tokens_per_s: float = 20.0
    write_burst_tokens: float = 60.0
    dry_run_account_reads: bool = False


@dataclass
class SelectionCfg:
    series_prefix: str = "KXAAAGAS"
    series_allowlist: List[str] = field(default_factory=list)
    max_markets: int = 5
    max_candidates_books: int = 40
    min_minutes_to_close: float = 120.0


@dataclass
class PostureCfg:
    size_fraction_of_target: float = 0.25
    min_side_size: int = 10
    ticks_behind_best_bid: int = 1
    tick: float = 0.01
    price_min: float = 0.03
    price_max: float = 0.97
    pull_minutes_before_close: float = 60.0
    require_paid_snapshot: bool = True
    when_below_reference: str = "skip"
    min_reprice_interval_s: float = 5.0
    maker_fee_per_contract: float = 0.0


@dataclass
class CapsCfg:
    max_total_at_risk_usd: float = 250.0
    max_per_market_at_risk_usd: float = 50.0
    max_markets: int = 5


@dataclass
class KillCfg:
    max_daily_loss_usd: float = 50.0
    max_drawdown_usd: float = 100.0
    max_total_loss_usd: float = 30.0
    position_tolerance_contracts: float = 0.0
    max_consecutive_api_errors: int = 3
    max_api_errors_per_10min: int = 10
    stale_book_s: float = 120.0
    max_clock_skew_s: float = 5.0
    pennying_max_reprices: int = 6
    pennying_window_s: float = 600.0
    queue_ahead_fraction_of_target: float = 0.2
    queue_ahead_persist_s: float = 300.0
    cusum_markout_horizon_s: float = 300.0
    cusum_k: float = 0.10
    cusum_h: float = 0.60


@dataclass
class LoopCfg:
    book_poll_s: float = 3.0
    reconcile_every_s: float = 60.0
    max_runtime_h: float = 16.0


@dataclass
class PathsCfg:
    log_dir: str = "data/live/lip_pilot"


@dataclass
class PilotConfig:
    dry_run: bool = True
    live: bool = False
    api: ApiCfg = field(default_factory=ApiCfg)
    selection: SelectionCfg = field(default_factory=SelectionCfg)
    posture: PostureCfg = field(default_factory=PostureCfg)
    caps: CapsCfg = field(default_factory=CapsCfg)
    kill: KillCfg = field(default_factory=KillCfg)
    loop: LoopCfg = field(default_factory=LoopCfg)
    paths: PathsCfg = field(default_factory=PathsCfg)

    def validate(self) -> "PilotConfig":
        c = self.caps
        if not (0 < c.max_total_at_risk_usd <= HARD_MAX_TOTAL_AT_RISK_USD):
            raise ValueError(f"caps.max_total_at_risk_usd must be in (0, {HARD_MAX_TOTAL_AT_RISK_USD}]")
        if not (0 < c.max_per_market_at_risk_usd <= HARD_MAX_PER_MARKET_AT_RISK_USD):
            raise ValueError(f"caps.max_per_market_at_risk_usd must be in (0, {HARD_MAX_PER_MARKET_AT_RISK_USD}]")
        if c.max_per_market_at_risk_usd > c.max_total_at_risk_usd:
            raise ValueError("per-market cap exceeds total cap")
        if not (1 <= c.max_markets <= HARD_MAX_MARKETS):
            raise ValueError(f"caps.max_markets must be in [1, {HARD_MAX_MARKETS}]")
        p = self.posture
        if p.when_below_reference not in ("skip", "join_reference"):
            raise ValueError("posture.when_below_reference must be skip|join_reference")
        if p.ticks_behind_best_bid < 1:
            raise ValueError("posture.ticks_behind_best_bid must be >= 1 (never join or cross the touch)")
        if not (0 < p.price_min < p.price_max < 1):
            raise ValueError("bad price band")
        if p.pull_minutes_before_close < 60:
            raise ValueError("posture.pull_minutes_before_close must be >= 60 (prereg)")
        if self.kill.max_daily_loss_usd <= 0 or self.kill.max_drawdown_usd <= 0 or self.kill.max_total_loss_usd <= 0:
            raise ValueError("loss limits must be positive")
        return self


def _build(cls, data: Optional[Mapping[str, Any]]):
    data = dict(data or {})
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"unknown config keys for {cls.__name__}: {sorted(unknown)}")
    kw = {}
    for name, f in known.items():
        if name not in data:
            continue
        v = data[name]
        ftype = f.default_factory if f.default_factory is not None and is_dataclass(f.default_factory) else None  # type: ignore[misc]
        if ftype is not None:
            kw[name] = _build(ftype, v)
        else:
            kw[name] = v
    return cls(**kw)


def load_config(path: Optional[Path] = None, overrides: Optional[Dict[str, Any]] = None) -> PilotConfig:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if overrides:
        raw = {**raw, **overrides}
    cfg = _build(PilotConfig, raw)
    if not isinstance(cfg.dry_run, bool) or not isinstance(cfg.live, bool):
        raise ValueError("dry_run and live must be booleans")
    return cfg.validate()


@dataclass(frozen=True)
class LiveGate:
    """Immutable record of the three gates, resolved once at startup. ``armed`` is True only when all
    three hold. It is frozen on purpose: a running live pilot must always be able to cancel its own
    orders, so changing the environment mid-run neither arms nor disarms it."""
    cli_live: bool
    env_ok: bool
    config_live: bool

    @property
    def armed(self) -> bool:
        return self.cli_live is True and self.env_ok is True and self.config_live is True

    def reasons(self) -> List[str]:
        out = []
        if not self.cli_live:
            out.append("missing --live CLI flag")
        if not self.env_ok:
            out.append(f"env {LIVE_ENV_VAR} != {LIVE_ENV_VALUE}")
        if not self.config_live:
            out.append("config needs live: true and dry_run: false")
        return out


def env_gate_ok(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(LIVE_ENV_VAR) == LIVE_ENV_VALUE


def resolve_gate(cfg: PilotConfig, cli_live: bool, environ: Optional[Mapping[str, str]] = None) -> LiveGate:
    return LiveGate(
        cli_live=bool(cli_live) is True,
        env_ok=env_gate_ok(environ),
        config_live=(cfg.live is True and cfg.dry_run is False),
    )
