"""
Tests for the whale-following re-test machinery:

- realistic fills (next print + latency, half-spread, Polymarket taker fee)
- point-in-time sizing (trailing volume, no lifetime volumeClob)
- position-level aggregation of partial exits
- favourite baseline signal generation
- calendar-daily Sharpe / Deflated Sharpe (Bailey & Lopez de Prado 2014)
- bootstrap CI on a mean difference
- the refactored simulate_whale_backtest loop (realistic is the default)
"""

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
# pytest prepends tests/ to sys.path, where tests/trading/ shadows src/trading/.
# Put src/ (and the repo root) first and drop a shadowing 'trading' package if present.
for p in (str(ROOT), str(ROOT / "src")):
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)
_tr = sys.modules.get("trading")
if _tr is not None and "tests" in Path(getattr(_tr, "__file__", "") or "").parts:
    for _k in [k for k in sys.modules if k == "trading" or k.startswith("trading.")]:
        del sys.modules[_k]

from src.whale_strategy.execution_model import (  # noqa: E402
    DEFAULT_FEE_RATES,
    FillConfig,
    SizingConfig,
    TradeTape,
    aggregate_partial_exits,
    entry_fill,
    market_exit_fill,
    size_position_pit,
    taker_fee_per_share,
)
from src.whale_strategy.retest_stats import (  # noqa: E402
    bootstrap_mean_diff_ci,
    daily_returns_full_calendar,
    deflated_sharpe_ratio,
    expected_max_sr,
)
from src.whale_strategy.favourite_baseline import build_favourite_baseline_signals  # noqa: E402
from src.whale_strategy.whale_config import WhaleConfig  # noqa: E402
from src.whale_strategy.whale_following_strategy import (  # noqa: E402
    StrategyState,
    WhaleSignal,
    calculate_position_size,
)


def _ts(s):
    return pd.Timestamp(s)


def _tape_df():
    # Market A: prints at t0, t0+2s, t0+10s, t0+1h. Market B: one print at t0+3s.
    t0 = _ts("2025-06-01 12:00:00")
    return pd.DataFrame({
        "market_id": ["A", "A", "A", "A", "B"],
        "datetime": [t0, t0 + pd.Timedelta(seconds=2), t0 + pd.Timedelta(seconds=10),
                     t0 + pd.Timedelta(hours=1), t0 + pd.Timedelta(seconds=3)],
        "price": [0.85, 0.86, 0.87, 0.90, 0.10],   # YES-normalised
        "usd_amount": [1000.0, 200.0, 300.0, 400.0, 999.0],
    }), t0


# ---------------------------------------------------------------- fees

def test_taker_fee_is_rate_times_p_times_one_minus_p():
    assert taker_fee_per_share(0.8, 0.04) == pytest.approx(0.04 * 0.8 * 0.2)
    assert taker_fee_per_share(0.5, 0.05) == pytest.approx(0.0125)
    assert taker_fee_per_share(0.9, 0.0) == 0.0


def test_fee_rates_by_category():
    cfg = FillConfig()
    assert cfg.fee_rate_for("Politics") == 0.04
    assert cfg.fee_rate_for("Finance") == 0.04
    assert cfg.fee_rate_for("Tech") == 0.04
    assert cfg.fee_rate_for("Economy") == 0.05
    assert cfg.fee_rate_for("Sports") == 0.05
    assert cfg.fee_rate_for("Geopolitics") == 0.0
    assert cfg.fee_rate_for("Art_and_Culture") == 0.05  # "other"
    assert DEFAULT_FEE_RATES["Geopolitics"] == 0.0
    custom = FillConfig(fee_rates={"Politics": 0.01}, default_fee_rate=0.02)
    assert custom.fee_rate_for("Politics") == 0.01
    assert custom.fee_rate_for("Sports") == 0.02


def test_fill_config_defaults_are_realistic():
    cfg = FillConfig()
    assert cfg.mode == "realistic"
    assert cfg.latency_s == 5.0
    assert cfg.half_spread == pytest.approx(0.01)


# ---------------------------------------------------------------- tape

def test_next_print_is_strictly_later_and_respects_latency():
    df, t0 = _tape_df()
    tape = TradeTape(df)
    # No latency: first print strictly after t0 in market A is t0+2s (not the t0 print itself)
    ts, px = tape.next_print("A", t0, latency_s=0.0)
    assert ts == t0 + pd.Timedelta(seconds=2) and px == pytest.approx(0.86)
    # 5s latency: skip t0+2s, take t0+10s
    ts, px = tape.next_print("A", t0, latency_s=5.0)
    assert ts == t0 + pd.Timedelta(seconds=10) and px == pytest.approx(0.87)
    # Same market only: market B print at t0+3s never used for A
    assert tape.next_print("B", t0 + pd.Timedelta(seconds=3), latency_s=0.0) is None
    assert tape.next_print("A", t0 + pd.Timedelta(hours=2)) is None
    assert tape.next_print("ZZZ", t0) is None


def test_trailing_volume_is_point_in_time():
    df, t0 = _tape_df()
    tape = TradeTape(df)
    # At t0+10s: prints at t0, t0+2s, t0+10s are <= t -> 1500; future t0+1h print excluded
    assert tape.trailing_volume("A", t0 + pd.Timedelta(seconds=10)) == pytest.approx(1500.0)
    # Window excludes prints older than 24h
    assert tape.trailing_volume("A", t0 + pd.Timedelta(hours=24.5)) == pytest.approx(400.0)
    assert tape.trailing_volume("A", t0 - pd.Timedelta(seconds=1)) == 0.0


def test_price_at_or_before():
    df, t0 = _tape_df()
    tape = TradeTape(df)
    assert tape.price_at_or_before("A", t0 + pd.Timedelta(seconds=5)) == pytest.approx(0.86)
    assert tape.price_at_or_before("A", t0 - pd.Timedelta(seconds=1)) is None


# ---------------------------------------------------------------- fills

def test_realistic_buy_fill_uses_next_print_plus_half_spread_and_fee():
    df, t0 = _tape_df()
    tape = TradeTape(df)
    cfg = FillConfig()  # latency 5s, half-spread 1c
    f = entry_fill("BUY", 0.85, t0, "A", "Politics", tape, cfg)
    assert f.ts == t0 + pd.Timedelta(seconds=10)
    assert f.yes_ref_price == pytest.approx(0.87)
    assert f.token_price == pytest.approx(0.88)
    assert f.fee_per_share == pytest.approx(0.04 * 0.88 * 0.12)


def test_realistic_sell_fill_buys_no_token():
    df, t0 = _tape_df()
    tape = TradeTape(df)
    f = entry_fill("SELL", 0.10, t0, "B", "Geopolitics", tape, FillConfig(latency_s=0.0))
    # Only B print is at t0+3s -> YES 0.10 -> NO 0.90 + 0.01
    assert f.token_price == pytest.approx(0.91)
    assert f.fee_per_share == 0.0  # Geopolitics fee-free


def test_realistic_fill_none_when_no_later_print_or_price_above_099():
    df, t0 = _tape_df()
    tape = TradeTape(df)
    assert entry_fill("BUY", 0.9, t0 + pd.Timedelta(hours=2), "A", "Tech", tape, FillConfig()) is None
    hi = pd.DataFrame({"market_id": ["C", "C"], "datetime": [t0, t0 + pd.Timedelta(seconds=30)],
                       "price": [0.97, 0.985], "usd_amount": [1.0, 1.0]})
    assert entry_fill("BUY", 0.97, t0, "C", "Tech", TradeTape(hi), FillConfig()) is None


def test_original_fill_is_whale_price_no_costs():
    df, t0 = _tape_df()
    f = entry_fill("BUY", 0.85, t0, "A", "Politics", TradeTape(df), FillConfig.original())
    assert f.token_price == pytest.approx(0.85)
    assert f.fee_per_share == 0.0
    assert f.ts == t0
    f2 = entry_fill("SELL", 0.15, t0, "A", "Politics", TradeTape(df), FillConfig.original())
    assert f2.token_price == pytest.approx(0.85)


def test_market_exit_fill_pays_half_spread_and_fee():
    f = market_exit_fill("BUY", 0.90, "Economy", FillConfig(), _ts("2025-01-01"))
    assert f.token_price == pytest.approx(0.89)
    assert f.fee_per_share == pytest.approx(0.05 * 0.89 * 0.11)
    f2 = market_exit_fill("SELL", 0.10, "Economy", FillConfig(), _ts("2025-01-01"))
    assert f2.token_price == pytest.approx(0.89)
    f3 = market_exit_fill("BUY", 0.90, "Economy", FillConfig.original(), _ts("2025-01-01"))
    assert f3.token_price == pytest.approx(0.90) and f3.fee_per_share == 0.0


# ---------------------------------------------------------------- sizing

def test_size_position_pit_caps():
    s = SizingConfig(volume_cap_pct=0.05, max_position_usd=250.0, min_position_usd=10.0)
    assert size_position_pit(100_000, 5_000, s) == pytest.approx(250.0)   # max position
    assert size_position_pit(2_000, 5_000, s) == pytest.approx(100.0)     # 5% of trailing vol
    assert size_position_pit(100_000, 120, s) == pytest.approx(120.0)     # available capital
    assert size_position_pit(100, 5_000, s) is None                       # 5$ < min 10$


def test_small_account_preset():
    s = SizingConfig.small_account()
    assert s.max_position_usd == 250.0
    assert s.capital == 5_000.0
    assert s.mode == "point_in_time"


def test_calculate_position_size_overrides():
    sig = WhaleSignal(market_id="m", category="Politics", whale_address="w", side="BUY",
                      price=0.85, size_usd=5000, score=9.0, datetime=_ts("2025-01-01"),
                      historical_winrate=0.99)
    state = StrategyState(total_capital=5_000)
    # Default limits: min position 5k -> skipped for a 5k account
    assert calculate_position_size(sig, state, 1_000_000) is None
    size = calculate_position_size(sig, state, 1_000, liquidity_cap_pct=0.05,
                                   min_position_usd=10, max_position_usd=250)
    assert size == pytest.approx(50.0)   # 5% of 1000 trailing volume
    size = calculate_position_size(sig, state, 1_000_000, liquidity_cap_pct=0.05,
                                   min_position_usd=10, max_position_usd=250)
    assert size <= 250.0


# ---------------------------------------------------------------- aggregation

def test_aggregate_partial_exits_counts_position_once():
    df = pd.DataFrame([
        {"position_id": 1, "market_id": "A", "entry_date": _ts("2025-01-01"), "exit_date": _ts("2025-01-03"),
         "direction": "BUY", "entry_price": 0.5, "exit_price": 0.8, "gross_pnl": 30.0, "net_pnl": 29.0,
         "fees_usd": 1.0, "position_size": 50.0, "reason": "PARTIAL_EXIT", "category": "Tech"},
        {"position_id": 1, "market_id": "A", "entry_date": _ts("2025-01-01"), "exit_date": _ts("2025-01-05"),
         "direction": "BUY", "entry_price": 0.5, "exit_price": 1.0, "gross_pnl": 50.0, "net_pnl": 49.0,
         "fees_usd": 1.0, "position_size": 50.0, "reason": "resolution", "category": "Tech"},
        {"position_id": 2, "market_id": "B", "entry_date": _ts("2025-01-02"), "exit_date": _ts("2025-01-04"),
         "direction": "SELL", "entry_price": 0.2, "exit_price": 0.0, "gross_pnl": -10.0, "net_pnl": -11.0,
         "fees_usd": 1.0, "position_size": 40.0, "reason": "resolution", "category": "Tech"},
    ])
    out = aggregate_partial_exits(df)
    assert len(out) == 2
    p1 = out[out["position_id"] == 1].iloc[0]
    assert p1["net_pnl"] == pytest.approx(78.0)
    assert p1["gross_pnl"] == pytest.approx(80.0)
    assert p1["position_size"] == pytest.approx(100.0)
    assert p1["exit_date"] == _ts("2025-01-05")
    assert p1["n_fills"] == 2
    assert p1["reason"] == "resolution"


# ---------------------------------------------------------------- baseline

def test_favourite_baseline_only_takes_favourites_without_whale_info():
    t0 = _ts("2025-03-01")
    prints = pd.DataFrame({
        "market_id": ["A", "A", "A", "B", "B", "C"],
        "datetime": [t0 + pd.Timedelta(minutes=i) for i in range(6)],
        "price": [0.50, 0.85, 0.99, 0.15, 0.40, 0.60],
        "usd_amount": [10.0] * 6,
        "category": ["Politics"] * 6,
        "maker": ["x"] * 6,
    })
    cfg = WhaleConfig()  # gates 0.80 / 0.20, max entry 0.98
    rng = np.random.default_rng(0)
    sigs = build_favourite_baseline_signals(prints, cfg, rng, one_per_market=True)
    by_mid = {s.market_id: s for s in sigs}
    assert set(by_mid) == {"A", "B"}          # C has no favourite print
    assert by_mid["A"].side == "BUY" and by_mid["A"].price == pytest.approx(0.85)  # 0.99 > max entry
    assert by_mid["B"].side == "SELL" and by_mid["B"].price == pytest.approx(0.15)
    every = build_favourite_baseline_signals(prints, cfg, rng, one_per_market=False)
    assert len(every) == 2


def test_favourite_baseline_respects_category_gates_and_min_print():
    t0 = _ts("2025-03-01")
    prints = pd.DataFrame({
        "market_id": ["A", "B"], "datetime": [t0, t0], "price": [0.75, 0.75],
        "usd_amount": [5000.0, 10.0], "category": ["Economy", "Politics"], "maker": ["x", "y"],
    })
    cfg = WhaleConfig(category_price_gates={"Economy": (0.70, 0.30)})
    sigs = build_favourite_baseline_signals(prints, cfg, np.random.default_rng(1))
    assert [s.market_id for s in sigs] == ["A"]
    assert build_favourite_baseline_signals(prints, cfg, np.random.default_rng(1), min_print_usd=10_000) == []


# ---------------------------------------------------------------- stats

def test_daily_returns_full_calendar_includes_zero_days():
    trades = pd.DataFrame({"exit_date": [_ts("2025-01-02"), _ts("2025-01-02"), _ts("2025-01-05")],
                           "net_pnl": [50.0, 50.0, -25.0]})
    r = daily_returns_full_calendar(trades, 1000.0, "2025-01-01", "2025-01-10")
    assert len(r) == 10
    assert r.loc[_ts("2025-01-02")] == pytest.approx(0.1)
    assert r.loc[_ts("2025-01-01")] == 0.0
    # Equity-based: day 5 return relative to equity 1100
    assert r.loc[_ts("2025-01-05")] == pytest.approx(-25.0 / 1100.0)


def test_expected_max_sr_grows_with_trials():
    assert expected_max_sr(1, 0.01) == 0.0
    assert expected_max_sr(100, 0.01) > expected_max_sr(10, 0.01) > 0


def test_deflated_sharpe_matches_formula():
    rng = np.random.default_rng(42)
    r = pd.Series(rng.normal(0.002, 0.01, 400))
    out = deflated_sharpe_ratio(r, n_trials=50)
    from scipy import stats
    sr = r.mean() / r.std(ddof=1)
    g3 = stats.skew(r)
    g4 = stats.kurtosis(r, fisher=False)
    n = len(r)
    v = 1.0 / (n - 1)
    emc = 0.5772156649015329
    sr0 = math.sqrt(v) * ((1 - emc) * stats.norm.ppf(1 - 1 / 50) + emc * stats.norm.ppf(1 - 1 / (50 * math.e)))
    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    assert out["n"] == n
    assert out["sr"] == pytest.approx(sr)
    assert out["sr0"] == pytest.approx(sr0)
    assert out["dsr"] == pytest.approx(stats.norm.cdf(z))
    # More trials -> lower DSR; one trial -> PSR against 0
    assert deflated_sharpe_ratio(r, n_trials=1000)["dsr"] < out["dsr"]
    assert deflated_sharpe_ratio(r, n_trials=1)["sr0"] == 0.0


def test_deflated_sharpe_zero_variance_series():
    out = deflated_sharpe_ratio(pd.Series([0.0] * 30), n_trials=10)
    assert out["sr"] == 0.0 and out["dsr"] == 0.0


def test_bootstrap_mean_diff_ci():
    rng = np.random.default_rng(0)
    a = rng.normal(0.10, 0.05, 200)
    b = rng.normal(0.00, 0.05, 200)
    out = bootstrap_mean_diff_ci(a, b, n_boot=2000, seed=1)
    assert out["lo"] > 0.05 and out["hi"] < 0.15
    assert out["diff"] == pytest.approx(a.mean() - b.mean())
    same = bootstrap_mean_diff_ci(b, b, n_boot=2000, seed=1)
    assert same["lo"] < 0 < same["hi"]
    # Repeatable
    assert bootstrap_mean_diff_ci(a, b, n_boot=500, seed=3) == bootstrap_mean_diff_ci(a, b, n_boot=500, seed=3)


def test_bootstrap_stratified_reweights_baseline():
    # Baseline has high returns in bucket "hi" only; whale entirely in bucket "lo".
    a = np.array([0.0] * 50)
    b = np.array([0.0] * 50 + [1.0] * 50)
    strata_a = np.array(["lo"] * 50)
    strata_b = np.array(["lo"] * 50 + ["hi"] * 50)
    out = bootstrap_mean_diff_ci(a, b, n_boot=500, seed=0, a_strata=strata_a, b_strata=strata_b)
    assert out["diff"] == pytest.approx(0.0)


# ---------------------------------------------------------------- simulate loop

def _load_script():
    spec = importlib.util.spec_from_file_location(
        "run_whale_category_backtest", ROOT / "scripts" / "backtest" / "run_whale_category_backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakePriceStore:
    def __init__(self, px):
        self.px = px

    def price_at_or_before(self, mid, ts):
        return self.px

    def close(self):
        pass


def _mini_ctx(mod):
    t0 = _ts("2025-06-02 10:00:00")
    prints = pd.DataFrame({
        "market_id": ["M"] * 3,
        "datetime": [t0, t0 + pd.Timedelta(seconds=3), t0 + pd.Timedelta(seconds=30)],
        "price": [0.85, 0.86, 0.88],
        "usd_amount": [2000.0, 500.0, 500.0],
    })
    sig = WhaleSignal(market_id="M", category="Politics", whale_address="w1", side="BUY", price=0.85,
                      size_usd=2000.0, score=9.0, datetime=t0, historical_winrate=0.95)
    res_date = _ts("2025-06-04 18:00:00")
    ctx = {
        "cfg": WhaleConfig(partial_exit_gain_threshold=0.0),
        "signals": [sig],
        "weekly_whale_sets": {},
        "resolutions": {"M": {"resolution": 1.0, "is_resolved": True, "resolution_date": res_date,
                              "market_close_date": res_date, "has_actual_resolution": True}},
        "scheduled_end_dates": {},
        "market_titles": {}, "market_slugs": {},
        "market_liquidity": {"M": 10_000_000.0},
        "price_store": _FakePriceStore(0.9),
        "tape": TradeTape(prints),
        "whales": {"w1"},
        "categories": ["Politics"],
        "test_start": _ts("2025-06-01"),
        "test_end": _ts("2025-06-10"),
    }
    return ctx, t0


def test_simulate_default_is_realistic_fill_with_fee():
    mod = _load_script()
    ctx, t0 = _mini_ctx(mod)
    sizing = SizingConfig(volume_cap_pct=0.05, max_position_usd=250.0, min_position_usd=10.0, use_kelly=False)
    res = mod.simulate_whale_backtest(ctx, capital=5_000, sizing=sizing)
    df = res["trades_df"]
    assert len(df) == 1
    row = df.iloc[0]
    # whale at 0.85; next print >= 5s later is 0.88; + 1c half spread
    assert row["entry_price"] == pytest.approx(0.89)
    # size = min(250, 5% of trailing 24h vol up to the decision time t0 = 0.05*2000=100)
    assert row["position_size"] == pytest.approx(100.0)
    shares = 100.0 / 0.89
    fee = shares * 0.04 * 0.89 * 0.11
    assert row["gross_pnl"] == pytest.approx((1.0 - 0.89) * shares)
    assert row["net_pnl"] == pytest.approx((1.0 - 0.89) * shares - fee)
    assert res["total_trades"] == 1
    # Daily returns span the full calendar of the test period
    assert len(res["daily_returns"]) == 10


def test_simulate_original_mode_reproduces_whale_price_fill():
    mod = _load_script()
    ctx, t0 = _mini_ctx(mod)
    sizing = SizingConfig(volume_cap_pct=0.05, max_position_usd=250.0, min_position_usd=10.0, use_kelly=False)
    res = mod.simulate_whale_backtest(ctx, capital=5_000, sizing=sizing, fill_config=FillConfig.original())
    row = res["trades_df"].iloc[0]
    assert row["entry_price"] == pytest.approx(0.85)
    # Original sizing at signal time: trailing vol = 2000 -> 100
    assert row["position_size"] == pytest.approx(100.0)
    gross = (1.0 - 0.85) * 100.0 / 0.85
    assert row["net_pnl"] == pytest.approx(gross * 0.97)


def test_simulate_accepts_baseline_signals_without_whale_filter():
    mod = _load_script()
    ctx, t0 = _mini_ctx(mod)
    ctx["weekly_whale_sets"] = {"2025-06-02": (set(), {}, {})}  # nobody active -> whale filter drops all
    sizing = SizingConfig(volume_cap_pct=0.05, max_position_usd=250.0, min_position_usd=10.0, use_kelly=False)
    res = mod.simulate_whale_backtest(ctx, capital=5_000, sizing=sizing)
    assert res.get("total_trades", 0) == 0
    res = mod.simulate_whale_backtest(ctx, capital=5_000, sizing=sizing, apply_whale_filter=False)
    assert res["total_trades"] == 1


def test_layering_never_exceeds_max_position_per_market():
    mod = _load_script()
    ctx, t0 = _mini_ctx(mod)
    big = pd.DataFrame({
        "market_id": ["M"] * 4,
        "datetime": [t0, t0 + pd.Timedelta(seconds=30), t0 + pd.Timedelta(minutes=10),
                     t0 + pd.Timedelta(minutes=11)],
        "price": [0.85, 0.86, 0.86, 0.87],
        "usd_amount": [100_000.0] * 4,
    })
    ctx["tape"] = TradeTape(big)
    sig2 = WhaleSignal(market_id="M", category="Politics", whale_address="w2", side="BUY", price=0.86,
                       size_usd=5000.0, score=9.0, datetime=t0 + pd.Timedelta(minutes=10),
                       historical_winrate=0.95)
    ctx["signals"] = ctx["signals"] + [sig2]
    sizing = SizingConfig.small_account()
    res = mod.simulate_whale_backtest(ctx, capital=5_000, sizing=sizing)
    assert res["total_trades"] == 1
    assert res["trades_df"].iloc[0]["position_size"] <= 250.0 + 1e-9


def test_research_price_store_falls_back_to_trades_for_markets_missing_from_prices(tmp_path):
    from src.whale_strategy.research_data_loader import ResearchPriceStore
    cat = tmp_path / "Economy"
    cat.mkdir()
    pd.DataFrame({"market_id": ["601697"], "outcome": ["YES"], "timestamp": [1_750_000_000],
                  "price": [0.5]}).to_parquet(cat / "prices.parquet")
    pd.DataFrame({"conditionId": ["0xabc", "0xabc"], "timestamp": [1_750_000_000, 1_750_000_100],
                  "price": [0.30, 0.20], "outcomeIndex": [0, 1]}).to_parquet(cat / "trades.parquet")
    ps = ResearchPriceStore(tmp_path, categories=["Economy"])
    day = pd.Timestamp(1_750_000_100, unit="s").normalize()
    assert ps.price_at_or_before("0xabc", day) == pytest.approx(0.80)   # NO print 0.20 -> YES 0.80
    assert ps.price_at_or_before("601697", day) == pytest.approx(0.5)
