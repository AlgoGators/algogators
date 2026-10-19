"""Order-book replay page: step through recorded order-book snapshots of one market.

The recorder (atlas) writes one folder per UTC day under ``<data>/live_books/<venue>/`` with
hourly ``books-HH.parquet`` snapshot files and ``trades-HH.parquet`` trade files. The page reads
only the day and hours you pick:

1. venue, day and an hour window; the market list shows the busiest books in that window
   (Polymarket books are per outcome token, labelled by event slug and outcome);
2. a time slider over the window picks a snapshot: the depth chart shows the cumulative bid and
   ask size by price at that moment, with the ladder as a table;
3. best bid / ask / mid over the window (downsampled to a few thousand points) with the
   recorder's trades overlaid (marker size ~ trade size), and a line at the selected time.

Prices are in the book's own outcome terms (the YES book for Kalshi; the token's outcome for
Polymarket).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from predict_ngin.viz import _ui, queries, sources


def _ns_to_dt(ns: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ns / 1e9, dt.timezone.utc)


def _arr(x) -> list:
    """A ladder column as a list (None / NaN when the side is empty)."""
    return [] if x is None or (isinstance(x, float) and np.isnan(x)) else list(x)


def _fmt(x) -> str:
    return "-" if x is None or pd.isna(x) else f"{x:.4g}"


def _depth_chart(snap: dict, title: str) -> None:
    bids = pd.DataFrame({"price": _arr(snap["bid_px"]), "size": _arr(snap["bid_sz"])}).dropna()
    asks = pd.DataFrame({"price": _arr(snap["ask_px"]), "size": _arr(snap["ask_sz"])}).dropna()
    bids = bids.sort_values("price", ascending=False).assign(cum=lambda d: d["size"].cumsum())
    asks = asks.sort_values("price").assign(cum=lambda d: d["size"].cumsum())
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=bids["price"],
            y=bids["cum"],
            name="bids",
            mode="lines",
            line_shape="hv",
            fill="tozeroy",
            line_color="#2ca02c",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=asks["price"],
            y=asks["cum"],
            name="asks",
            mode="lines",
            line_shape="hv",
            fill="tozeroy",
            line_color="#d62728",
        )
    )
    fig.update_layout(
        title=title, xaxis_title="price", yaxis_title="cumulative size", height=420, hovermode="x"
    )
    st.plotly_chart(fig)
    ladder = pd.concat(
        [
            bids.rename(columns={"price": "bid", "size": "bid size", "cum": "bid cum"}).reset_index(
                drop=True
            ),
            asks.rename(columns={"price": "ask", "size": "ask size", "cum": "ask cum"}).reset_index(
                drop=True
            ),
        ],
        axis=1,
    )
    st.dataframe(ladder, hide_index=True, height=260)


def _top_chart(top: pd.DataFrame, trades: pd.DataFrame | None, t_sel: dt.datetime) -> None:
    t = pd.to_datetime(top["ts_ns"], unit="ns", utc=True)
    fig = go.Figure()
    for col, color in (("best_bid", "#2ca02c"), ("best_ask", "#d62728"), ("mid", "#7f7f7f")):
        fig.add_trace(
            go.Scatter(
                x=t,
                y=top[col],
                name=col.replace("_", " "),
                mode="lines",
                line_shape="hv",
                line_color=color,
                line_dash="dot" if col == "mid" else "solid",
            )
        )
    if trades is not None and not trades.empty:
        size = trades["size"].astype(float)
        fig.add_trace(
            go.Scatter(
                x=pd.to_datetime(trades["ts_recv_ns"], unit="ns", utc=True),
                y=trades["price"],
                mode="markers",
                name="trades",
                marker={
                    "size": 4 + 16 * np.sqrt(size / max(size.max(), 1e-9)),
                    "color": "#1f77b4",
                    "opacity": 0.6,
                },
                text=trades["taker_side"].astype(str) + " " + size.round(2).astype(str),
                hovertemplate="%{x}<br>%{y}<br>taker %{text}<extra></extra>",
            )
        )
    fig.add_vline(x=t_sel, line_dash="dash", line_color="black")
    fig.update_layout(
        title="Top of book",
        height=420,
        yaxis_title="price",
        hovermode="x unified",
        legend_orientation="h",
    )
    st.plotly_chart(fig)


def render() -> None:
    st.title("Order book replay")
    venue = st.radio("Venue", list(sources.VENUES), horizontal=True, key="ob_venue")
    if not _ui.need(f"{venue}_books"):
        return
    days = sources.recorder_days(venue)
    if not days:
        st.info(f"No recorder day folders under `{sources.recorder_dir(venue)}`.")
        return
    c1, c2 = st.columns([1, 2])
    day = c1.selectbox("Day (UTC)", days[::-1], key="ob_day")
    h0, h1 = c2.slider("Hours (UTC)", 0, 24, (0, 24), key="ob_hours")
    if h1 <= h0:
        st.warning("Pick at least one hour.")
        return
    hours = range(h0, h1)
    books = sources.books_source(venue, day, hours)
    if books is None:
        st.info("No book files for those hours.")
        return
    day_lo, _ = sources.day_bounds_ns(day)
    lo_ns, hi_ns = day_lo + h0 * 3600 * 10**9, day_lo + h1 * 3600 * 10**9

    mkts = _ui.query(
        queries.book_markets(
            f"(SELECT * FROM {books} WHERE {queries.ts_range('ts_recv_ns', lo_ns, hi_ns)})"
        ),
        "Listing markets",
    )
    if mkts is None or mkts.empty:
        st.info("No snapshots in that window.")
        return
    labels = []
    for r in mkts.itertuples():
        pair = r.pair if isinstance(r.pair, str) and r.pair else None
        mid = str(r.market_id)
        name = f"{pair} ({mid[:10]}…)" if pair and len(mid) > 30 else (pair or mid)
        labels.append(f"{name}  ·  {r.outcome}  ·  {r.snapshots:,} snapshots")
    i = st.selectbox("Market", range(len(mkts)), format_func=lambda k: labels[k], key="ob_mkt")
    row = mkts.iloc[i]
    market_id = str(row.market_id)
    outcome = row.outcome if isinstance(row.outcome, str) and row.outcome else None
    first, last = int(row.first_ns), int(row.last_ns)

    t_sel = st.slider(
        "Snapshot time (UTC)",
        min_value=_ns_to_dt(first),
        max_value=_ns_to_dt(last) if last > first else _ns_to_dt(first + 10**9),
        value=_ns_to_dt(last),
        step=dt.timedelta(seconds=1),
        format="HH:mm:ss",
        key=f"ob_t_{venue}_{day}_{market_id}_{outcome}",
    )
    t_ns = int(t_sel.timestamp() * 1e9) + 999_999_999  # include every snapshot in that second

    snap = _ui.query(queries.book_snapshot_at(books, market_id, outcome, t_ns), "Loading snapshot")
    if snap is not None and not snap.empty:
        s = snap.iloc[0].to_dict()
        at = _ns_to_dt(int(s["ts_recv_ns"])).strftime("%H:%M:%S.%f")[:-3]
        bb, ba = s["best_bid"], s["best_ask"]
        c1, c2, c3 = st.columns(3)
        c1.metric("best bid", _fmt(bb))
        c2.metric("best ask", _fmt(ba))
        c3.metric("spread", _fmt(ba - bb) if pd.notna(bb) and pd.notna(ba) else "-")
        _depth_chart(s, f"Book at {day} {at} UTC")

    top = _ui.query(
        queries.book_top_series(books, market_id, outcome, lo_ns, hi_ns), "Loading top of book"
    )
    trades = None
    if _ui.has(f"{venue}_live_trades"):
        tsrc = sources.live_trades_source(venue, day, hours)
        if tsrc is not None:
            trades = _ui.query(
                queries.live_trades(tsrc, market_id, outcome, lo_ns, hi_ns), "Loading trades"
            )
    if top is not None and not top.empty:
        _top_chart(top, trades, t_sel)
    if trades is not None:
        st.caption(f"{len(trades):,} recorder trades in the window.")
