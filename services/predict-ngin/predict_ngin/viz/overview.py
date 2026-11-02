"""Overview page: what data is on this machine, and how much traded per month.

* **Data status** - every catalog view and whether it exists here, the lake manifest (rows,
  months, last contract check), and the text of ``predict-ngin data status``.
* **Monthly volume** - stacked bars per venue and category for the chosen date range.
  Polymarket comes from the hourly bars (fast). Months the bars do not cover yet (a lake build
  in progress, or no lake) can optionally be computed from the maker fills, one month at a
  time, which is slower. Kalshi comes from the Kalshi bars when built, else from the trade tape
  (taker-paid notional), grouped by category when the Kalshi lake market table exists and by
  series otherwise.
"""

from __future__ import annotations

import contextlib
import io

import pandas as pd
import plotly.express as px
import streamlit as st

from predict_ngin import lake
from predict_ngin.viz import _ui, queries, sources


def _status_text() -> str:
    from predict_ngin.lake import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.status()
    return buf.getvalue()


@st.cache_data(show_spinner=False, ttl=300)
def _status_cached() -> str:
    try:
        return _status_text()
    except Exception as e:  # status reads many optional files; never break the page on it
        return f"(status unavailable: {e})"


def _data_status() -> None:
    st.subheader("Data status")
    views = pd.DataFrame(lake.views(_ui.connection()))
    views["build with"] = [
        "" if a else sources.BUILD_HINTS.get(v, "")
        for v, a in zip(views.view, views.available, strict=True)
    ]
    st.dataframe(views, hide_index=True)
    m = lake.manifest.load()
    if m:
        rows = []
        for name, e in sorted(m.items()):
            lv = e.get("last_verify") or {}
            rows.append(
                {
                    "dataset": name,
                    "rows": e.get("rows"),
                    "updated": e.get("updated"),
                    "last verify": ("OK" if lv.get("ok") else "FAILED") if lv else "",
                    "verified at": lv.get("at", ""),
                }
            )
        st.caption("Lake manifest (`<data>/lake/_manifest.json`)")
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    for venue in sources.VENUES:
        months = sources.bar_months(venue, "1h")
        if months:
            st.caption(f"{venue} hourly bars: {len(months)} months ({months[0]}..{months[-1]})")
    with st.expander("`uv run predict-ngin data status`"):
        st.code(_status_cached(), language="text")


def _pm_volume(start: int, end: int, exclude_updown: bool) -> pd.DataFrame | None:
    frames = []
    have = set(sources.bar_months("polymarket", "1h"))
    wanted = sources.months_between(start, end)
    covered = [m for m in wanted if m in have]
    missing = [m for m in wanted if m not in have]
    if covered:
        src = sources.bars_source("polymarket", "1h", start, end)
        assert src is not None  # covered
        df = _ui.query(
            queries.pm_monthly_volume_from_bars(src, start, end, exclude_updown),
            "Polymarket volume from hourly bars",
        )
        if df is not None:
            frames.append(df)
    if missing:
        st.caption(
            f"Polymarket months without bars: {_ui.caption_list(missing)} "
            "(`uv run predict-ngin data build polymarket`)."
        )
        if (
            _ui.has("polymarket_markets")
            and _ui.has("polymarket_maker_fills")
            and st.checkbox(
                f"Compute the {len(missing)} missing month(s) from fills (a few seconds per month)",
                key="ov_pm_fills",
            )
        ):
            for m in missing:
                lo, hi = sources.month_bounds(m)
                lo, hi = max(start, lo), min(end, hi)
                src = sources.pm_fills_source(lo, hi)
                if src is None:
                    continue
                df = _ui.query(
                    queries.pm_monthly_volume_from_fills(
                        src, "polymarket_markets", lo, hi, exclude_updown
                    ),
                    f"Polymarket {m} from fills",
                )
                if df is not None:
                    frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def _kalshi_volume(start: int, end: int) -> pd.DataFrame | None:
    lake_mk = "kalshi_lake_markets" if _ui.has("kalshi_lake_markets") else None
    if sources.bars_cover("kalshi", "1h", start, end):
        src = sources.bars_source("kalshi", "1h", start, end)
        sql = (
            "SELECT strftime(to_timestamp(ts), '%Y-%m') AS month, "
            "coalesce(category, 'Unknown') AS category, sum(notional) AS usd, "
            "sum(contracts) AS contracts, sum(trades) AS trades "
            f"FROM {src} WHERE {queries.ts_range('ts', start, end)} GROUP BY 1, 2 ORDER BY 1, 2"
        )
        return _ui.query(sql, "Kalshi volume from hourly bars")
    if not _ui.need("kalshi_trades"):
        return None
    src = sources.kalshi_trades_source(start, end)
    if src is None:
        st.info("No Kalshi trades in this range.")
        return None
    return _ui.query(
        queries.kalshi_monthly_volume(src, start, end, lake_mk),
        "Kalshi volume from the trade tape (about 2 s per month on first load)",
    )


def _chart(df: pd.DataFrame, title: str, n: int = 10) -> None:
    df = _ui.top_n(df, "category", "usd", n)
    df = df.groupby(["month", "category"], as_index=False)["usd"].sum()
    fig = px.bar(df, x="month", y="usd", color="category", title=title)
    fig.update_layout(yaxis_title="USD", xaxis_title=None, legend_title=None, height=420)
    st.plotly_chart(fig)


def render() -> None:
    st.title("Overview")
    _data_status()

    st.subheader("Monthly volume")
    c1, c2 = st.columns([2, 1])
    with c1:
        start, end = _ui.date_range("Date range (UTC)", 180, "ov_range")
    with c2:
        exclude_updown = st.checkbox("Exclude Polymarket crypto up/down", key="ov_updown")
        venues = st.multiselect("Venues", list(sources.VENUES), list(sources.VENUES))
    totals = []
    if "polymarket" in venues:
        if sources.bar_months("polymarket", "1h") or _ui.has("polymarket_maker_fills"):
            pm = _pm_volume(start, end, exclude_updown)
            if pm is not None and not pm.empty:
                _chart(pm, "Polymarket: maker-fill USD volume by category")
                totals.append(pm.assign(venue="polymarket"))
        else:
            _ui.need("polymarket_bars_1h")
    if "kalshi" in venues:
        ks = _kalshi_volume(start, end)
        if ks is not None and not ks.empty:
            group = "category" if _ui.has("kalshi_lake_markets") else "series"
            _chart(ks, f"Kalshi: taker-paid notional by {group}")
            totals.append(ks.assign(venue="kalshi"))
    if totals:
        t = pd.concat(totals).groupby(["month", "venue"], as_index=False)["usd"].sum()
        fig = px.line(t, x="month", y="usd", color="venue", markers=True, title="Venue totals")
        fig.update_layout(yaxis_title="USD", xaxis_title=None, height=320)
        st.plotly_chart(fig)
        wide = t.pivot(index="month", columns="venue", values="usd").fillna(0)
        st.dataframe(wide.map(_ui.fmt_usd))
