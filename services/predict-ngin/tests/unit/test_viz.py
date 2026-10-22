"""Data explorer: the SQL builders (predict_ngin.viz.queries) and bounded file sources
(predict_ngin.viz.sources) run against tiny synthetic tables named like the lake views.

No Streamlit needed: the page modules are layout only.
"""

from __future__ import annotations

import calendar

import duckdb
import pandas as pd
import pytest

from predict_ngin.viz import queries as Q
from predict_ngin.viz import sources as S

JAN = calendar.timegm((2026, 1, 1, 0, 0, 0))
FEB = calendar.timegm((2026, 2, 1, 0, 0, 0))
MAR = calendar.timegm((2026, 3, 1, 0, 0, 0))
DAY = 86400


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute(
        """
        CREATE TABLE polymarket_markets AS SELECT * FROM (VALUES
          ('m1', 'Will A win?', 'a-win', 'Event A', 'Politics', FALSE, '1', '[''1'', ''0'']',
           '1000', '2026-01-01 00:00:00+00', '2026-02-10 00:00:00+00', 'e1'),
          ('m2', 'Will B win?', 'b-win', 'Event B', 'Sports', FALSE, 'true', '["0", "1"]',
           '500', '2026-01-01 00:00:00+00', '2026-02-10 00:00:00+00', 'e2'),
          ('m3', 'Bitcoin Up or Down', 'btc-updown', 'BTC', 'Crypto', TRUE, '1',
           '[''1'', ''0'']', '9000', NULL, NULL, 'e3'),
          ('m4', 'Still open 50% market', 'open', 'Event D', 'Politics', FALSE, '0',
           '[''0.5'', ''0.5'']', NULL, NULL, NULL, 'e4')
        ) t(market_id, question, slug, event_title, category, is_updown, closed,
            outcome_prices, volume, created_at, end_date, event_id)
        """
    )
    # hourly bars: m1 trades at 0.8 then converges to 0.99; m2 at 0.2 then 0.01; m3 up/down
    rows = []
    for mid, cat, ev, upd, p0, p1 in (
        ("m1", "Politics", "e1", False, 0.8, 0.99),
        ("m2", "Sports", "e2", False, 0.2, 0.01),
        ("m3", "Crypto", "e3", True, 0.5, 0.5),
    ):
        for k, (ts, p) in enumerate(((JAN + 10 * DAY, p0), (JAN + 12 * DAY, p1))):
            rows.append((cat, ev, mid, upd, ts, p, p, p, p, p, 2, 100.0 + k, 50.0, 60.0, 40.0))
    bars = pd.DataFrame(
        rows,
        columns="category event_id market_id is_updown ts open high low close vwap fills usd "
        "tokens buy_usd sell_usd".split(),
    )
    bars["year"], bars["month"] = 2026, "01"
    c.register("bars_df", bars)
    c.execute("CREATE TABLE polymarket_bars_1h AS SELECT * FROM bars_df")
    c.execute(
        "CREATE TABLE polymarket_maker_fills AS SELECT market_id, CAST(ts AS DOUBLE) AS ts_chain, "
        "close AS yes_price, 'buy' AS yes_side, usd AS usd_amount, tokens AS token_amount, "
        "'0xa' AS maker, '0xb' AS taker FROM bars_df"
    )
    c.execute(
        f"""
        CREATE TABLE kalshi_trades AS SELECT * FROM (VALUES
          ('t1', 'KXRAIN-26JAN10-NYC', 'KXRAIN-26JAN10', 0.7, 0.3, 10, 10.0, 'yes', {JAN + DAY}),
          ('t2', 'KXRAIN-26JAN10-NYC', 'KXRAIN-26JAN10', 0.9, 0.1, 5, 5.0, 'no', {JAN + 3 * DAY}),
          ('t3', 'KXFED-26JAN-T4', 'KXFED-26JAN', 0.3, 0.7, 2, 2.5, 'yes', {JAN + DAY}),
          ('t4', 'KXFED-26JAN-T4', 'KXFED-26JAN', 0.05, 0.95, 1, 1.0, 'no', {JAN + 3 * DAY}),
          ('t5', 'KXFED-26JAN-T4', 'KXFED-26JAN', 0.05, 0.95, 1, NULL, 'no', {FEB + DAY})
        ) t(trade_id, ticker, event_ticker, yes_price, no_price, count, count_fp, taker_side,
            timestamp_unix)
        """
    )
    c.execute(
        """
        CREATE TABLE kalshi_markets AS SELECT * FROM (VALUES
          ('KXRAIN-26JAN10-NYC', 'KXRAIN-26JAN10', 'Rain in NYC?', 'finalized', 'yes',
           '2026-01-01T00:00:00Z', '2026-01-10T00:00:00Z'),
          ('KXFED-26JAN-T4', 'KXFED-26JAN', 'Fed above 4%?', 'finalized', 'no',
           '2026-01-01T00:00:00Z', '2026-01-30T00:00:00Z')
        ) t(ticker, event_ticker, title, status, result, open_time, close_time)
        """
    )
    c.execute(
        "CREATE TABLE kalshi_lake_markets AS SELECT ticker, event_ticker, "
        "split_part(event_ticker, '-', 1) AS series, "
        "CASE WHEN ticker LIKE 'KXRAIN%' THEN 'Weather' ELSE 'Economics' END AS category, "
        "title, result, status FROM kalshi_markets"
    )
    c.execute(
        "CREATE TABLE kalshi_bars_1h AS SELECT 'Weather' AS category, 'KXRAIN' AS series, "
        "event_ticker, ticker, (timestamp_unix // 3600) * 3600 AS ts, yes_price AS open, "
        "yes_price AS high, yes_price AS low, yes_price AS close, yes_price AS vwap, "
        "1 AS trades, count_fp AS contracts, count_fp * yes_price AS notional, "
        "2026 AS year, '01' AS month FROM kalshi_trades WHERE timestamp_unix < 1769904000"
    )
    ns = 10**9
    c.execute(
        f"""
        CREATE TABLE polymarket_books AS SELECT * FROM (VALUES
          ('tok1', 'yes', 'slug-a', {JAN * ns}, 0.40, 0.42, [0.40, 0.39], [100.0, 50.0],
           [0.42, 0.43], [80.0, 20.0]),
          ('tok1', 'yes', 'slug-a', {(JAN + 60) * ns}, 0.41, 0.43, [0.41], [10.0],
           [0.43], [5.0]),
          ('tok2', 'no', 'slug-a', {(JAN + 30) * ns}, 0.57, 0.59, [0.57], [1.0], [0.59], [2.0])
        ) t(market_id, outcome, pair, ts_recv_ns, best_bid, best_ask, bid_px, bid_sz, ask_px,
            ask_sz)
        """
    )
    c.execute(
        f"""
        CREATE TABLE polymarket_live_trades AS SELECT * FROM (VALUES
          ('tok1', 'yes', {(JAN + 30) * ns}, 0.42, 12.0, 'buy'),
          ('tok1', 'yes', {(JAN + 90) * ns}, 0.43, 3.0, 'buy'),
          ('tok2', 'no', {(JAN + 30) * ns}, 0.58, 1.0, 'sell')
        ) t(market_id, outcome, ts_recv_ns, price, size, taker_side)
        """
    )
    yield c
    c.close()


def df(con, sql: str) -> pd.DataFrame:
    return con.sql(sql).df()


# ------------------------------------------------------------------------------------- helpers
def test_literals_escape_quotes_and_wildcards():
    assert Q.lit("it's") == "'it''s'"
    assert Q.lit_list([]) == "(NULL)"
    assert Q.like_pattern("50%_x") == "'%50\\%\\_x%'"
    assert Q.ts_range("ts", None, None) == "TRUE"
    assert Q.ts_range("ts", 1, 2.9) == "ts >= 1 AND ts < 2"


def test_month_partition_filter_end_is_exclusive(con):
    f = Q.month_partition_filter(JAN, FEB)  # [Jan 1, Feb 1) -> January only
    assert "202601" in f and "202602" not in f
    assert df(con, f"SELECT count(*) n FROM polymarket_bars_1h WHERE {f}").n[0] == 6


def test_months_between_and_bounds():
    assert S.months_between(JAN, MAR) == ["2026-01", "2026-02"]
    assert S.months_between(JAN + DAY, FEB + 1) == ["2026-01", "2026-02"]
    assert S.month_bounds("2026-12") == (
        calendar.timegm((2026, 12, 1, 0, 0, 0)),
        calendar.timegm((2027, 1, 1, 0, 0, 0)),
    )


# ------------------------------------------------------------------------------------ overview
def test_pm_monthly_volume_bars_and_fills_agree(con):
    a = df(con, Q.pm_monthly_volume_from_bars("polymarket_bars_1h", JAN, FEB))
    fills_sql = Q.pm_monthly_volume_from_fills(
        "polymarket_maker_fills", "polymarket_markets", JAN, FEB
    )
    b = df(con, fills_sql)
    assert set(a.category) == {"Politics", "Sports", "Crypto"}
    assert a.usd.sum() == pytest.approx(603.0)
    assert a.set_index("category").usd.to_dict() == pytest.approx(
        b.set_index("category").usd.to_dict()
    )
    no_updown = df(con, Q.pm_monthly_volume_from_bars("polymarket_bars_1h", JAN, FEB, True))
    assert "Crypto" not in set(no_updown.category)


def test_kalshi_monthly_volume_taker_notional(con):
    out = df(con, Q.kalshi_monthly_volume("kalshi_trades", JAN, MAR))
    jan = out[out.month == "2026-01"].set_index("category")
    # KXRAIN: 10 * 0.7 (yes taker) + 5 * 0.1 (no taker)
    assert jan.loc["KXRAIN", "usd"] == pytest.approx(7.5)
    assert jan.loc["KXFED", "contracts"] == pytest.approx(3.5)
    # count_fp NULL falls back to count
    assert out[out.month == "2026-02"].usd.sum() == pytest.approx(0.95)
    by_cat = df(con, Q.kalshi_monthly_volume("kalshi_trades", JAN, FEB, "kalshi_lake_markets"))
    assert set(by_cat.category) == {"Weather", "Economics"}


# ------------------------------------------------------------------------------ market explorer
def test_pm_search(con):
    out = df(con, Q.pm_search("polymarket_markets", "win", 10))
    assert list(out.market_id) == ["m1", "m2"]  # volume order
    assert list(df(con, Q.pm_search("polymarket_markets", "m3", 10)).market_id) == ["m3"]
    assert df(con, Q.pm_search("polymarket_markets", "bitcoin", 10, True)).empty
    assert df(con, Q.pm_search("polymarket_markets", "100%", 10)).empty  # no wildcard match


def test_kalshi_search(con):
    out = df(con, Q.kalshi_search("kalshi_markets", "rain", 10))
    assert list(out.ticker) == ["KXRAIN-26JAN10-NYC"]
    out = df(con, Q.kalshi_search("kalshi_lake_markets", "kxfed", 10, has_category=True))
    assert out.category.tolist() == ["Economics"]


def test_bars_and_trades_series(con):
    bars = df(
        con,
        Q.bars_series(
            "polymarket_bars_1h", "market_id", "m1", JAN, FEB, "usd", ["event_id = 'e1'"]
        ),
    )
    assert bars.close.tolist() == [0.8, 0.99]
    built = df(
        con,
        Q.trades_series(
            "polymarket_maker_fills", "market_id", "m1", "ts_chain", "yes_price", "usd_amount",
            JAN, FEB, 3600,
        ),
    )
    assert built.close.tolist() == [0.8, 0.99] and built.ts.tolist() == bars.ts.tolist()
    k = df(
        con,
        Q.trades_series(
            "kalshi_trades", "ticker", "KXRAIN-26JAN10-NYC", "timestamp_unix", "yes_price",
            "coalesce(count_fp, count)", JAN, FEB, 86400,
        ),
    )
    assert k.volume.tolist() == [10.0, 5.0]
    kb = df(con, Q.bars_series("kalshi_bars_1h", "ticker", "KXFED-26JAN-T4", JAN, FEB, "contracts"))
    assert kb.volume.tolist() == [2.5, 1.0]


def test_recent_trades_newest_first(con):
    out = df(
        con,
        Q.recent_trades(
            "kalshi_trades", "ticker", "KXFED-26JAN-T4", "timestamp_unix",
            ["trade_id", "yes_price"], JAN, MAR, 2,
        ),
    )
    assert out.trade_id.tolist() == ["t5", "t4"]


# ---------------------------------------------------------------------------------- calibration
def test_pm_outcomes_parse_outcome_prices(con):
    out = df(con, Q.pm_outcomes("polymarket_markets", exclude_updown=False)).set_index("market_id")
    assert out.yes_won.to_dict() == {"m1": 1, "m2": 0, "m3": 1}  # m4 unresolved
    only = df(con, Q.pm_outcomes("polymarket_markets", ["Sports"]))
    assert only.market_id.tolist() == ["m2"]


def test_calibration_picks_price_before_horizon(con):
    obs = Q.pm_price_obs_from_bars("polymarket_bars_1h", JAN, FEB)
    out = df(con, Q.calibration(obs, Q.pm_outcomes("polymarket_markets"), DAY, 0.05))
    # one observation per market, at least a day before its last bar: 0.8 (won), 0.2 (lost)
    assert out.markets.sum() == 2
    assert out.avg_price.tolist() == pytest.approx([0.2, 0.8])
    assert out.win_rate.tolist() == [0.0, 1.0]
    assert (out.ci_lo <= out.win_rate).all() and (out.win_rate <= out.ci_hi).all()
    # zero horizon takes the converged last price instead
    late = df(con, Q.calibration(obs, Q.pm_outcomes("polymarket_markets"), 0, 0.05))
    assert late.avg_price.tolist() == pytest.approx([0.01, 0.99])
    # price exactly 1.0 lands in the top band, not an extra one
    one = df(
        con,
        Q.calibration(
            "(SELECT 'x' AS market_id, 0 AS ts, 1.0 AS price, 1.0 AS usd)",
            "(SELECT 'x' AS market_id, 'c' AS category, 1 AS yes_won)",
            0,
            0.1,
        ),
    )
    assert one.band_hi.tolist() == pytest.approx([1.0])
    # fills give the same observations as bars here
    fills = Q.pm_price_obs_from_fills("polymarket_maker_fills", JAN, FEB)
    same = df(con, Q.calibration(fills, Q.pm_outcomes("polymarket_markets"), DAY, 0.05))
    assert same.avg_price.tolist() == pytest.approx(out.avg_price.tolist())


def test_kalshi_calibration(con):
    outcomes = Q.kalshi_outcomes("kalshi_markets", series_prefix="kx")
    obs = Q.kalshi_price_obs("kalshi_trades", JAN, FEB)
    out = df(con, Q.calibration(obs, outcomes, DAY, 0.1))
    assert out.markets.sum() == 2
    assert dict(zip(out.avg_price.round(2), out.win_rate, strict=True)) == {0.3: 0.0, 0.7: 1.0}
    cat = Q.kalshi_outcomes("kalshi_lake_markets", categories=["Weather"], has_category=True)
    assert df(con, cat).market_id.tolist() == ["KXRAIN-26JAN10-NYC"]
    bars = Q.kalshi_price_obs_from_bars("kalshi_bars_1h", JAN, FEB)
    assert df(con, Q.calibration(bars, outcomes, DAY, 0.1)).markets.sum() == 2


# ---------------------------------------------------------------------------------- order books
def test_book_queries(con):
    ns = 10**9
    mk = df(con, Q.book_markets("polymarket_books"))
    assert mk.iloc[0].market_id == "tok1" and mk.iloc[0].snapshots == 2
    snap = df(con, Q.book_snapshot_at("polymarket_books", "tok1", "yes", (JAN + 59) * ns))
    assert snap.best_bid[0] == 0.40 and list(snap.bid_sz[0]) == [100.0, 50.0]
    snap = df(con, Q.book_snapshot_at("polymarket_books", "tok1", None, (JAN + 600) * ns))
    assert snap.best_bid[0] == 0.41
    top = df(con, Q.book_top_series("polymarket_books", "tok1", "yes", JAN * ns, (JAN + 120) * ns))
    assert top.mid.tolist() == pytest.approx([0.41, 0.42])
    tr = df(con, Q.live_trades("polymarket_live_trades", "tok1", "yes", JAN * ns, (JAN + 60) * ns))
    assert tr.price.tolist() == [0.42]


# ---------------------------------------------------------------------------- bounded sources
def test_sources_read_only_the_months_and_hours_in_range(tmp_path, monkeypatch, con):
    monkeypatch.setenv("PREDICT_NGIN_DATA", str(tmp_path))
    assert S.bar_months("polymarket") == [] and S.bars_source("polymarket", "1h", JAN, FEB) is None
    assert S.recorder_days("kalshi") == []
    for y, m in ((2026, "01"), (2026, "02")):
        d = tmp_path / "lake" / "polymarket" / "bars_1h" / f"year={y}" / f"month={m}"
        d.mkdir(parents=True)
        con.execute(
            f"COPY (SELECT * EXCLUDE (year, month) FROM polymarket_bars_1h) "
            f"TO '{(d / 'part-00.parquet').as_posix()}' (FORMAT parquet)"
        )
    (tmp_path / "lake" / "polymarket" / "bars_1h" / "year=2026" / "month=03.building").mkdir()
    assert S.bar_months("polymarket") == ["2026-01", "2026-02"]
    assert S.bars_cover("polymarket", "1h", JAN, MAR)
    assert not S.bars_cover("polymarket", "1h", JAN, MAR + DAY)
    src = S.bars_source("polymarket", "1h", JAN, FEB)
    assert "month=02" not in src
    out = df(con, Q.pm_monthly_volume_from_bars(src, JAN, FEB))
    assert out.usd.sum() == pytest.approx(603.0)  # hive year/month columns usable

    day = tmp_path / "live_books" / "polymarket" / "2026-01-01"
    day.mkdir(parents=True)
    for h in ("00", "05"):
        con.execute(
            f"COPY (SELECT * FROM polymarket_books) TO '{(day / f'books-{h}.parquet').as_posix()}'"
            " (FORMAT parquet)"
        )
    assert S.recorder_days("polymarket") == ["2026-01-01"]
    assert "books-05" not in S.books_source("polymarket", "2026-01-01", range(0, 2))
    assert S.live_trades_source("polymarket", "2026-01-01") is None
    assert df(con, Q.book_markets(S.books_source("polymarket", "2026-01-01"))).snapshots.sum() == 6
    assert S.day_bounds_ns("2026-01-01") == (JAN * 10**9, (JAN + DAY) * 10**9)


def test_viz_package_has_no_streamlit_in_query_modules():
    import ast
    from pathlib import Path

    import predict_ngin.viz as pkg

    root = Path(pkg.__file__).parent
    for name in ("queries.py", "sources.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        mods = {
            (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names
        }
        assert not any(m.startswith(("streamlit", "plotly")) for m in mods), name


def test_app_pages_degrade_gracefully_on_an_empty_data_folder(tmp_path, monkeypatch):
    """Every page renders without exceptions when no dataset exists, and says how to get one."""
    pytest.importorskip("streamlit")
    pytest.importorskip("plotly")
    from pathlib import Path

    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import predict_ngin.viz as pkg

    monkeypatch.setenv("PREDICT_NGIN_DATA", str(tmp_path))
    st.cache_data.clear()
    st.cache_resource.clear()
    at = AppTest.from_file(str(Path(pkg.__file__).parent / "app.py"), default_timeout=60)
    at.run()
    for page in ("Overview", "Market explorer", "Calibration", "Order book replay"):
        at.sidebar.radio(key="page").set_value(page).run()
        assert not at.exception, (page, [e.value for e in at.exception])
        if page != "Overview":
            assert any("To create it" in i.value for i in at.info), page
    st.cache_data.clear()
    st.cache_resource.clear()
