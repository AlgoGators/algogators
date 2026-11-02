"""Calibration page: do markets priced at p resolve YES a fraction p of the time?

For every resolved market with trades in the window, take one observation: its last YES price
at least *horizon* hours before its last trade in the window (the last trade stands in for the
resolution time, so prices that have already converged to 0 or 1 are left out). Markets are
grouped into price bands; the chart plots each band's average price against the share that
resolved YES, with 95% Wilson intervals, against the diagonal of perfect calibration.

* **Polymarket** outcomes come from ``outcome_prices`` in ``polymarket_markets`` (closed markets
  whose first outcome price is 0 or 1); prices from the hourly bars when they cover the window,
  else from maker fills (slower; keep the window short). Crypto up/down markets are excluded by
  default: there are millions of them and they would swamp everything else.
* **Kalshi** outcomes come from ``result`` (yes/no) in ``kalshi_lake_markets`` or
  ``kalshi_markets``; prices from the Kalshi bars when built, else from the trade tape.

This is a descriptive view, not a backtest: no fees, no fill model, and markets that resolved
after the window but traded in it are judged on trades inside the window only.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from predict_ngin.viz import _ui, queries, sources

# Windows without bars read every trade. Longer windows take minutes and can exhaust DuckDB's
# memory / temp limits (a 365-day Polymarket fill window crashed DuckDB 1.5 on a 4 GB limit).
MAX_FILL_DAYS = 92
MAX_TAPE_DAYS = 184


def _cap(start: int, end: int, days: int, what: str) -> int:
    if end - start <= days * 86400:
        return start
    st.warning(
        f"Prices come from {what} here (no bars for this window), so the window is limited to "
        f"the last {days} days. Build the lake bars for longer windows."
    )
    return end - days * 86400


@st.cache_data(show_spinner=False, ttl=3600)
def _pm_categories() -> list:
    df = _ui.run(
        "SELECT DISTINCT category FROM polymarket_markets WHERE category IS NOT NULL ORDER BY 1"
    )
    return df["category"].tolist()


def _polymarket(start: int, end: int):
    if not _ui.need("polymarket_markets"):
        return None
    c1, c2 = st.columns([3, 1])
    cats = c1.multiselect("Categories (empty = all)", _pm_categories(), key="cal_pm_cats")
    exclude = c2.checkbox("Exclude crypto up/down", value=True, key="cal_pm_updown")
    outcomes = queries.pm_outcomes("polymarket_markets", cats, exclude)
    if sources.bars_cover("polymarket", "1h", start, end):
        src = sources.bars_source("polymarket", "1h", start, end)
        assert src is not None  # bars_cover checked
        obs = queries.pm_price_obs_from_bars(src, start, end, exclude)
        how = "hourly bar closes"
    else:
        missing = [
            m
            for m in sources.months_between(start, end)
            if m not in set(sources.bar_months("polymarket", "1h"))
        ]
        st.caption(
            f"No hourly bars for {_ui.caption_list(missing)}; using maker fills "
            "(several seconds per month)."
        )
        if not _ui.need("polymarket_maker_fills"):
            return None
        start = _cap(start, end, MAX_FILL_DAYS, "Polymarket fills")
        src = sources.pm_fills_source(start, end)
        if src is None:
            return None
        obs = queries.pm_price_obs_from_fills(src, start, end)
        how = "maker fills"
    return obs, outcomes, how


def _kalshi(start: int, end: int):
    has_lake = _ui.has("kalshi_lake_markets")
    table = "kalshi_lake_markets" if has_lake else "kalshi_markets"
    if not _ui.need(table):
        return None
    c1, c2 = st.columns([1, 2])
    prefix = c1.text_input("Series / ticker prefix (e.g. KXRAIN)", key="cal_k_prefix")
    cats: list = []
    if has_lake:
        all_cats = _ui.run(
            "SELECT DISTINCT category FROM kalshi_lake_markets WHERE category IS NOT NULL "
            "ORDER BY 1"
        )["category"].tolist()
        cats = c2.multiselect("Categories (empty = all)", all_cats, key="cal_k_cats")
    else:
        c2.caption(
            "Categories need the Kalshi lake market table "
            "(`uv run predict-ngin data build kalshi --markets`)."
        )
    outcomes = queries.kalshi_outcomes(table, prefix, cats, has_lake)
    if sources.bars_cover("kalshi", "1h", start, end):
        src = sources.bars_source("kalshi", "1h", start, end)
        assert src is not None  # bars_cover checked
        return queries.kalshi_price_obs_from_bars(src, start, end), outcomes, "hourly bar closes"
    if not _ui.need("kalshi_trades"):
        return None
    start = _cap(start, end, MAX_TAPE_DAYS, "the Kalshi trade tape")
    src = sources.kalshi_trades_source(start, end)
    if src is None:
        return None
    return queries.kalshi_price_obs(src, start, end), outcomes, "trades"


def _chart(df: pd.DataFrame, title: str) -> None:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[0, 1],
            y=[0, 1],
            mode="lines",
            name="perfect calibration",
            line={"dash": "dash", "color": "gray"},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["avg_price"],
            y=df["win_rate"],
            mode="markers+lines",
            name="observed",
            error_y={
                "type": "data",
                "symmetric": False,
                "array": df["ci_hi"] - df["win_rate"],
                "arrayminus": df["win_rate"] - df["ci_lo"],
            },
            marker={"size": 6 + 18 * (df["markets"] / df["markets"].max()) ** 0.5},
            customdata=df[["markets", "band_lo", "band_hi"]],
            hovertemplate="band %{customdata[1]:.2f}-%{customdata[2]:.2f}<br>avg price %{x:.3f}"
            "<br>YES rate %{y:.3f}<br>%{customdata[0]} markets<extra></extra>",
        )
    )
    fig.update_layout(
        title=title,
        height=560,
        xaxis_title="average price in band (YES)",
        yaxis_title="share resolved YES",
        xaxis_range=[0, 1],
        yaxis_range=[0, 1],
    )
    st.plotly_chart(fig)


def render() -> None:
    st.title("Calibration")
    venue = st.radio("Venue", list(sources.VENUES), horizontal=True, key="cal_venue")
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        # Kalshi has no bars until its lake is built; the tape is ~2 s per month to scan
        default_days = 365 if venue == "polymarket" else 90
        start, end = _ui.date_range("Trade window (UTC)", default_days, f"cal_range_{venue}")
    horizon = c2.number_input("Horizon (hours before last trade)", 0, 24 * 30, 24, key="cal_h")
    band = c3.selectbox("Band width", [0.05, 0.1, 0.02], key="cal_band")
    min_usd = c4.number_input("Min market volume (USD)", 0.0, 1e9, 100.0, key="cal_min")

    got = _polymarket(start, end) if venue == "polymarket" else _kalshi(start, end)
    if got is None:
        return
    obs, outcomes, how = got
    sql = queries.calibration(obs, outcomes, int(horizon * 3600), float(band), float(min_usd))
    df = _ui.query(sql, "Computing calibration")
    if df is None:
        return
    if df.empty:
        st.warning("No resolved markets with trades in this window and filter.")
        return
    n = int(df["markets"].sum())
    _chart(df, f"{venue}: {n:,} resolved markets, prices from {how}, {horizon} h horizon")
    gap = (df["win_rate"] - df["avg_price"]).abs()
    st.caption(
        f"Market-weighted mean |YES rate - price| = {(gap * df['markets']).sum() / n:.3f}. "
        "Bands with few markets have wide intervals."
    )
    st.dataframe(df.round(4), hide_index=True)
    with st.expander("SQL"):
        st.code(sql, language="sql")
