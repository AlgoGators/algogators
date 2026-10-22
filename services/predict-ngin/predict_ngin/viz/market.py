"""Market explorer page: find one market and look at its price path and latest trades.

1. Pick a venue and search: Polymarket searches question, slug, event title and market id in
   ``polymarket_markets``; Kalshi searches ticker, event ticker and title in
   ``kalshi_lake_markets`` (every ticker in the tape) or, before the Kalshi lake is built,
   ``kalshi_markets`` (markets seen since 2026-01-28). A Kalshi ticker typed exactly is offered
   even when no market table has it.
2. The date range defaults to the market's own life (capped at 90 days before its end).
3. The price chart uses the lake bars (1-minute for ranges up to three days, hourly otherwise)
   when bar files exist for every month in range; otherwise bars are built on the fly from the
   fills / trades of just those months. Volume (USD for Polymarket, contracts for Kalshi) is
   drawn underneath.
4. The table lists the most recent trades: the two days up to the last trade in range.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from predict_ngin.viz import _ui, queries, sources

AUTO_1M_DAYS = 3


def _to_date(value) -> Optional[dt.date]:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            ts = pd.to_datetime(int(value), unit="s", utc=True)
        else:
            ts = pd.to_datetime(str(value), utc=True, errors="coerce")
    except (ValueError, OverflowError):
        return None
    return None if pd.isna(ts) else ts.date()


def _jsonable(v):
    if isinstance(v, (list, tuple, np.ndarray)):
        return [str(x) for x in v]
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    return str(v)


def _search_polymarket() -> Optional[dict]:
    if not _ui.need("polymarket_markets"):
        return None
    c1, c2 = st.columns([3, 1])
    text = c1.text_input("Search question / slug / event / market id", key="mx_pm_q")
    exclude = c2.checkbox("Hide crypto up/down", value=True, key="mx_pm_updown")
    if not text.strip():
        st.caption("Type part of a market question, e.g. `election` or `bitcoin`.")
        return None
    df = _ui.query(queries.pm_search("polymarket_markets", text, 50, exclude), "Searching")
    if df is None or df.empty:
        st.warning("No matching markets.")
        return None
    labels = [
        f"{r.question}  [{r.category or '-'} · id {r.market_id} · vol {_ui.fmt_usd(r.volume or 0)}]"
        for r in df.itertuples()
    ]
    i = st.selectbox("Market", range(len(df)), format_func=lambda k: labels[k], key="mx_pm_sel")
    row = df.iloc[i].to_dict()
    return {
        "venue": "polymarket",
        "id": str(row["market_id"]),
        "title": row["question"],
        "start": _to_date(row.get("created_at")),
        "end": _to_date(row.get("end_date")),
        "event_id": row.get("event_id"),
        "meta": row,
    }


def _search_kalshi() -> Optional[dict]:
    has_lake = _ui.has("kalshi_lake_markets")
    table = "kalshi_lake_markets" if has_lake else "kalshi_markets"
    text = st.text_input("Search ticker / event ticker / title", key="mx_k_q")
    if not text.strip():
        st.caption("Type part of a title or ticker, e.g. `KXRAIN` or `rain in Dallas`.")
        return None
    df = pd.DataFrame()
    if _ui.has(table):
        res = _ui.query(queries.kalshi_search(table, text, 50, has_lake), "Searching")
        df = res if res is not None else df
    else:
        st.caption(
            "No Kalshi market table here (`uv run predict-ngin data update kalshi` saves one); "
            "type an exact ticker."
        )
    t = text.strip().upper()
    if " " not in t and "-" in t and (df.empty or t not in set(df.ticker)):
        df = pd.concat(
            [
                pd.DataFrame(
                    [{"ticker": t, "event_ticker": t.rsplit("-", 1)[0], "title": "(typed)"}]
                ),
                df,
            ],
            ignore_index=True,
        )
    if df.empty:
        st.warning("No matching markets.")
        return None
    labels = [f"{r.ticker}  {r.title or ''}" for r in df.itertuples()]
    i = st.selectbox("Market", range(len(df)), format_func=lambda k: labels[k], key="mx_k_sel")
    row = df.iloc[i].to_dict()
    meta = row
    start = end = None
    if _ui.has("kalshi_markets"):
        m = _ui.query(
            "SELECT * FROM kalshi_markets WHERE ticker = " + queries.lit(row["ticker"]),
            "Loading market",
        )
        if m is not None and not m.empty:
            meta = {**m.iloc[0].to_dict(), **{k: v for k, v in row.items() if v is not None}}
            start, end = _to_date(meta.get("open_time")), _to_date(meta.get("close_time"))
    end = end or _to_date(meta.get("close_ts"))
    return {
        "venue": "kalshi",
        "id": row["ticker"],
        "title": row.get("title") or row["ticker"],
        "start": start,
        "end": end,
        "event_id": None,
        "meta": meta,
    }


def _price_series(mk: dict, start: int, end: int, size: str) -> tuple[Optional[pd.DataFrame], str]:
    venue, mid = mk["venue"], mk["id"]
    bucket = 60 if size == "1m" else 3600
    if sources.bars_cover(venue, size, start, end):
        src = sources.bars_source(venue, size, start, end)
        assert src is not None  # bars_cover checked
        if venue == "polymarket":
            extra = [f"event_id = {queries.lit(mk['event_id'])}"] if mk.get("event_id") else []
            sql = queries.bars_series(src, "market_id", mid, start, end, "usd", extra)
        else:
            sql = queries.bars_series(src, "ticker", mid, start, end, "contracts")
        return _ui.query(sql, f"Loading {size} bars"), f"lake bars ({size})"
    if venue == "polymarket":
        if not _ui.need("polymarket_maker_fills"):
            return None, ""
        src = sources.pm_fills_source(start, end)
        if src is None:
            return None, ""
        sql = queries.trades_series(
            src, "market_id", mid, "ts_chain", "yes_price", "usd_amount", start, end, bucket
        )
    else:
        if not _ui.need("kalshi_trades"):
            return None, ""
        src = sources.kalshi_trades_source(start, end)
        if src is None:
            return None, ""
        sql = queries.trades_series(
            src,
            "ticker",
            mid,
            "timestamp_unix",
            "yes_price",
            "coalesce(count_fp, count)",
            start,
            end,
            bucket,
        )
    return _ui.query(sql, "Building bars from trades"), f"built from trades ({size} buckets)"


def _recent(mk: dict, start: int, end: int) -> Optional[pd.DataFrame]:
    if mk["venue"] == "polymarket":
        src = sources.pm_fills_source(start, end)
        cols = ["yes_price", "yes_side", "usd_amount", "token_amount", "maker", "taker"]
        sql = src and queries.recent_trades(
            src, "market_id", mk["id"], "ts_chain", cols, start, end
        )
    else:
        src = sources.kalshi_trades_source(start, end)
        cols = ["trade_id", "yes_price", "no_price", "count_fp", "taker_side"]
        sql = src and queries.recent_trades(
            src, "ticker", mk["id"], "timestamp_unix", cols, start, end
        )
    return _ui.query(sql, "Loading recent trades") if sql else None


def _chart(df: pd.DataFrame, title: str, volume_label: str, candles: bool) -> None:
    t = pd.to_datetime(df["ts"], unit="s", utc=True)
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.03
    )
    if candles:
        fig.add_trace(
            go.Candlestick(
                x=t,
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="YES price",
            ),
            row=1,
            col=1,
        )
        fig.update_layout(xaxis_rangeslider_visible=False)
    else:
        fig.add_trace(
            go.Scatter(x=t, y=df["close"], name="close", mode="lines", line_shape="hv"),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(x=t, y=df["vwap"], name="VWAP", mode="lines", opacity=0.6),
            row=1,
            col=1,
        )
    fig.add_trace(go.Bar(x=t, y=df["volume"], name=volume_label, opacity=0.7), row=2, col=1)
    fig.update_yaxes(title_text="YES price", range=[0, 1], row=1, col=1)
    fig.update_yaxes(title_text=volume_label, row=2, col=1)
    fig.update_layout(title=title, height=620, hovermode="x unified", legend_orientation="h")
    st.plotly_chart(fig)


def render() -> None:
    st.title("Market explorer")
    venue = st.radio("Venue", list(sources.VENUES), horizontal=True, key="mx_venue")
    mk = _search_polymarket() if venue == "polymarket" else _search_kalshi()
    if mk is None:
        return
    with st.expander("Market details"):
        st.json({k: _jsonable(v) for k, v in mk["meta"].items()}, expanded=False)

    end_d = min(mk["end"] or _ui.today(), _ui.today()) + dt.timedelta(days=1)
    start_d = max(mk["start"] or end_d - dt.timedelta(days=90), end_d - dt.timedelta(days=90))
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        start, end = _ui.date_range(
            "Date range (UTC)", (end_d - start_d).days, f"mx_range_{venue}_{mk['id']}", end_d
        )
    res = c2.radio("Resolution", ["auto", "1h", "1m"], horizontal=True, key="mx_res")
    candles = c3.checkbox("Candlesticks", key="mx_candles")
    size = res if res != "auto" else ("1m" if end - start <= AUTO_1M_DAYS * 86400 else "1h")

    df, how = _price_series(mk, start, end, size)
    if df is None:
        return
    if df.empty:
        st.warning("No trades for this market in the selected range.")
        return
    vol = "USD" if venue == "polymarket" else "contracts"
    _chart(df, mk["title"], vol, candles)
    st.caption(
        f"{len(df):,} bars, {how}. Volume {df['volume'].sum():,.0f} {vol}; "
        f"last close {df['close'].iloc[-1]:.3f}."
    )

    st.subheader("Most recent trades")
    last = int(df["ts"].max())
    lo, hi = max(start, last - 2 * 86400), min(end, last + (60 if size == "1m" else 3600))
    trades = _recent(mk, lo, hi)
    if trades is not None:
        st.dataframe(trades, hide_index=True)
