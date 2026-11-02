"""Data lake: readers, builder, contracts, catalog and CLI on a tiny synthetic data folder (no real data needed).

Covers: old-vs-new reader equivalence (golden, D2), the Polymarket lake build (partitioning, sort, reconciliation,
contract failure leaves the old month in place), lake/legacy switching in the readers, month pruning, Kalshi
count_fp and gap detection, catalog views and `predict-ngin data status`.
"""
from __future__ import annotations

import datetime as dt
import importlib
import json

import duckdb
import pandas as pd
import pytest

from tests.unit import _legacy_kalshi_tape as old_k
from tests.unit import _legacy_polymarket_tape as old_pm

D20251230 = 1767052800  # Polymarket seam
JAN = 1735689600  # 2025-01-01
FEB = 1738368000  # 2025-02-01
JAN26 = 1767225600  # 2026-01-01
KCUT = 1769558400  # Kalshi cutover 2026-01-28


def _pm_rows(ts_list, market_ids, branch):
    rows = []
    for i, (ts, mid) in enumerate(zip(ts_list, market_ids, strict=True)):
        r = dict(block_number=1000 + i, ts_local=int(ts), ts_chain=float(ts), timestamp_unreliable=False,
                 transaction_hash=("0xab" if branch == "ext" else "AB") + f"{i:04d}",  # ext rows: lowercase, as built
                 market_id=mid, condition_id=f"c{mid}", token_id=f"t{mid}", outcome_index=0.0, outcome0_label="Yes",
                 price_raw=0.4, yes_price=0.4 + 0.01 * (i % 5), yes_side="BUY", is_taker_order_row=(i % 4 == 3),
                 usd_amount=10.0 + i, token_amount=25.0, maker=f"0xMAKER{i}", taker=f"0xTAKER{i}",
                 token_conflict=False)
        if branch == "old":
            r["order_hash"] = f"oh{i}"
        else:
            r.update(log_index=i, contract="ctf", event_id=f"e{mid}", nonusdc_side="token1",
                     maker_direction="sell", taker_direction="buy", source="hf")
        rows.append(r)
    return pd.DataFrame(rows)


@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("PREDICT_NGIN_DATA", str(d))
    monkeypatch.delenv("PREDICT_NGIN_COLD", raising=False)
    # Polymarket: old clean tape (Jan + Feb 2025, two markets) and ext rows after the seam
    (d / "polymarket_clean/trades").mkdir(parents=True)
    (d / "polymarket_clean/trades_ext/hf").mkdir(parents=True)
    old = _pm_rows([JAN + 3600 * k for k in range(6)] + [FEB + 3600 * k for k in range(6)],
                   ["101", "102"] * 6, "old")
    old.to_parquet(d / "polymarket_clean/trades/part-0000.parquet", index=False)
    ext = _pm_rows([D20251230 - 3600] + [JAN26 + 3600 * k for k in range(5)], ["101"] + ["103"] * 5, "ext")
    ext.to_parquet(d / "polymarket_clean/trades_ext/hf/part-rg00001.parquet", index=False)
    (d / "polymarket_clean/trades_ext/coverage.json").write_text(json.dumps({"end_ts": JAN26 + 86400}))
    # Polymarket market list + event tags
    (d / "polymarket").mkdir()
    pd.DataFrame({"id": ["101", "102", "103"], "question": ["q1", "q2", "btc up?"],
                  "slug": ["will-x", "will-y", "btc-updown-5m-1"], "condition_id": ["c101", "c102", "c103"],
                  "event_id": ["e101", "e102", "e103"], "event_slug": ["ex", "ey", "btc-updown-5m-1"],
                  "event_title": ["X", "Y", "BTC"], "closed": [True, True, False],
                  "outcome_prices": ["['1', '0']"] * 3}).to_parquet(d / "polymarket/markets.parquet", index=False)
    pd.DataFrame({"event_id": ["e101", "e102"], "slug": ["ex", "ey"], "title": ["X", "Y"], "category_raw": [None, None],
                  "closed": [True, True], "tag_labels": [["Politics"], ["Sports", "NBA"]],
                  "tag_slugs": [["politics"], ["sports"]]}).to_parquet(d / "polymarket_clean/event_tags.parquet",
                                                                      index=False)
    # Kalshi: one old monthly file + two ext day files (one with count_fp)
    (d / "kalshi/trades_fixed").mkdir(parents=True)
    (d / "kalshi/trades_fixed_ext").mkdir(parents=True)

    def k(ids, ts, cnt, fp=None):
        f = pd.DataFrame({"trade_id": ids, "ticker": ["KXA-1"] * len(ids), "event_ticker": ["KXA"] * len(ids),
                          "yes_price": [0.3] * len(ids), "no_price": [0.7] * len(ids), "count": cnt,
                          "taker_side": ["yes"] * len(ids), "created_time": ["x"] * len(ids),
                          "timestamp_unix": ts, "timestamp_unix_orig": ts})
        if fp is not None:
            f["count_fp"] = fp
        return f

    k(["a", "b", "dup"], [KCUT - 100, KCUT - 50, KCUT + 10], [1, 2, 3]).to_parquet(
        d / "kalshi/trades_fixed/trades_2026-01.parquet", index=False)
    k(["dup", "c"], [KCUT + 10, KCUT + 20], [3, 4], [3.0, 4.0]).to_parquet(
        d / "kalshi/trades_fixed_ext/trades_2026-01-28.parquet", index=False)
    k(["e"], [KCUT + 2 * 86400], [0], [0.4]).to_parquet(d / "kalshi/trades_fixed_ext/trades_2026-01-30.parquet",
                                                         index=False)
    return d


def _mods():
    import predict_ngin.lake.build_polymarket as b
    import predict_ngin.tapes.kalshi as k
    import predict_ngin.tapes.polymarket as p

    return importlib.reload(k), importlib.reload(p), importlib.reload(b)


def _q(con, sql):
    return con.sql(sql).fetchall()


# --------------------------------------------------------------------------------------- readers (golden)
def test_readers_match_frozen_copies(data):
    k, p, _ = _mods()
    con = duckdb.connect()
    kd = dict(old_dir=data / "kalshi/trades_fixed", ext_dir=data / "kalshi/trades_fixed_ext")
    for kw in [{}, {"columns": ["ticker", "yes_price"]}, {"start_ts": KCUT}, {"end_ts": KCUT}]:
        a = _q(con, f"SELECT * FROM ({old_k.tape_sql(**kw, **kd)}) ORDER BY 1")
        b = _q(con, f"SELECT * FROM ({k.tape_sql(**kw)}) ORDER BY 1")
        assert a == b
    pd_ = dict(old_dir=data / "polymarket_clean/trades", ext_dir=data / "polymarket_clean/trades_ext")
    for kw in [{}, {"maker_rows_only": True}, {"start_ts": FEB}, {"end_ts": FEB, "columns": ["market_id", "usd_amount"]}]:
        assert old_pm.clean_trades_sql(**kw, **pd_) == p.clean_trades_sql(**kw)


def test_kalshi_no_duplicate_ids_and_count_fp(data):
    k, _, _ = _mods()
    rows = duckdb.sql(f"SELECT trade_id, count, count_fp FROM ({k.tape_sql(k.ALL_COLUMNS)}) ORDER BY 1").fetchall()
    assert [r[0] for r in rows] == ["a", "b", "c", "dup", "e"]
    fp = {r[0]: r[2] for r in rows}
    assert fp["a"] == 1.0 and fp["e"] == 0.4  # old rows: count_fp = count; ext rows keep the exact value


def test_kalshi_missing_days(data):
    k, _, _ = _mods()
    gaps = k.missing_days(until=dt.date(2026, 1, 31))
    assert gaps == [dt.date(2026, 1, 29), dt.date(2026, 1, 31)]


# ---------------------------------------------------------------------------------------- lake build
def test_polymarket_lake_build_and_switch(data):
    k, p, b = _mods()
    legacy = duckdb.sql(f"SELECT count(*), sum(usd_amount) FROM ({p.clean_trades_sql(maker_rows_only=True)})").fetchone()
    assert not p.lake_ready()
    b.build_markets()
    mk = pd.read_parquet(data / "lake/polymarket/markets.parquet").set_index("market_id")
    assert mk.loc["101", "category"] == "Politics" and mk.loc["102", "category"] == "Sports"
    assert mk.loc["103", "category"] == "Crypto" and bool(mk.loc["103", "is_updown"])
    b.build_fills()
    assert p.lake_ready()
    months = sorted(x.parent.name + "/" + x.name for x in (data / "lake/polymarket/fills").glob("year=*/month=*"))
    assert months == ["year=2025/month=01", "year=2025/month=02", "year=2026/month=01"]
    lake = duckdb.sql(f"SELECT count(*), sum(usd_amount) FROM ({p.clean_trades_sql(maker_rows_only=True)})").fetchone()
    assert lake == legacy  # the reader now serves the lake, with the same rows
    assert "lake/polymarket/fills" in p.clean_trades_sql().replace("\\", "/")
    # month pruning: a February window opens only the February folder
    sql = p.clean_trades_sql(start_ts=FEB, end_ts=FEB + 86400).replace("\\", "/")
    assert "month=02" in sql and "month=01" not in sql
    # sorted by category inside the file
    cats = duckdb.sql(f"SELECT category FROM read_parquet('{(data / 'lake/polymarket/fills/year=2025/month=01').as_posix()}/*.parquet')").fetchall()
    assert [c[0] for c in cats] == sorted(c[0] for c in cats)
    # lake-only columns are requestable now
    assert duckdb.sql(f"SELECT count(*) FROM ({p.clean_trades_sql(['category'])}) WHERE category = 'Sports'").fetchone()[0] == 6
    # bars add up to the maker fills, per month
    from predict_ngin import lake as lk

    con = lk.connect()
    for v in ("polymarket_bars_1h", "polymarket_bars_1m"):
        assert con.sql(f"SELECT sum(fills), round(sum(usd), 6) FROM {v}").fetchone() == (
            legacy[0], round(legacy[1], 6))
    jan_1h = con.sql("SELECT count(*) FROM polymarket_bars_1h WHERE ts < 1738368000").fetchone()[0]
    assert jan_1h == 6 - 1  # one Jan fill is a taker-order row; each remaining fill is its own hour
    state = json.loads((data / "lake/polymarket/fills_state.json").read_text())
    assert state["_complete"] and state["2025-01"]["rows"] == 6


def test_contract_failure_keeps_old_month(data):
    _, p, b = _mods()
    b.build_markets()
    b.build_fills()
    f = data / "polymarket_clean/trades/part-0000.parquet"
    bad = pd.read_parquet(f)
    bad.loc[0, "yes_price"] = 2.0
    bad.to_parquet(f, index=False)
    with pytest.raises(SystemExit, match="check failed"):
        b.build_fills(["2025-01"], rebuild=True)
    assert (data / "lake/polymarket/fills/year=2025/month=01").is_dir()  # old month still served
    assert (data / "lake/polymarket/fills/year=2025/month=01.building").is_dir()  # failed build left for inspection


def test_contract_runner_reports_violations():
    from predict_ngin.lake import contracts

    con = duckdb.connect()
    sql = ("SELECT 't1' AS trade_id, 'K' AS ticker, 'E' AS event_ticker, 0.3::DOUBLE AS yes_price, "
           "0.7::DOUBLE AS no_price, 1::BIGINT AS count, 1.0::DOUBLE AS count_fp, 'yes' AS taker_side, "
           "1::BIGINT AS timestamp_unix UNION ALL SELECT 't1', 'K', 'E', 1.5, 0.7, 1, 0.0, 'up', 2")
    failed = {r["check"] for r in contracts.failed(contracts.KALSHI_TRADES.run(con, sql))}
    assert {"prices in [0, 1]", "count_fp > 0", "taker_side yes/no", "unique (trade_id)"} <= failed


# ---------------------------------------------------------------------------------------- catalog + CLI
def test_catalog_and_status(data, capsys):
    _mods()
    from predict_ngin import lake
    from predict_ngin.lake import cli

    con = lake.connect()
    avail = {v["view"]: v["available"] for v in lake.views(con)}
    assert avail["kalshi_trades"] and avail["polymarket_maker_fills"] and not avail["kalshi_books"]
    assert con.sql("SELECT count(*) FROM kalshi_trades").fetchone()[0] == 5
    assert cli.main(["data", "status"]) == 0
    out = capsys.readouterr().out
    assert "kalshi trades" in out and "days missing" in out


def test_kalshi_lake_build_served_only_without_gaps(data, monkeypatch):
    k, _, _ = _mods()
    import predict_ngin.lake.build_kalshi as bk

    bk = importlib.reload(bk)
    (data / "vendor/kalshi_research").mkdir(parents=True)
    pd.DataFrame({"series_ticker": ["KXA"], "category": ["Sports"], "title": ["A"], "frequency": ["daily"],
                  "fee_type": ["quadratic"], "fee_multiplier": [1.0]}).to_parquet(
        data / "vendor/kalshi_research/series.parquet", index=False)
    legacy = duckdb.sql(f"SELECT count(*), sum(count_fp) FROM ({k.tape_sql(k.ALL_COLUMNS)})").fetchone()
    bk.build_trades()
    assert not k.lake_ready()  # fixture has missing days: built, but not served
    monkeypatch.setattr(bk.tape, "missing_days", lambda *a, **kw: [])
    bk.build_trades()
    assert k.lake_ready()
    sql = k.tape_sql(k.ALL_COLUMNS)
    assert "lake/kalshi/trades" in sql.replace("\\", "/")
    assert duckdb.sql(f"SELECT count(*), sum(count_fp) FROM ({sql})").fetchone() == legacy
    cats = duckdb.sql(f"SELECT DISTINCT category FROM read_parquet('{(data / 'lake/kalshi/trades').as_posix()}/*/*/*.parquet')").fetchall()
    assert cats == [("Sports",)]
    bars = duckdb.sql(f"SELECT sum(trades), sum(contracts) FROM read_parquet('{(data / 'lake/kalshi/bars_1h').as_posix()}/*/*/*.parquet')").fetchone()
    assert bars == legacy


def test_retired_months_are_never_rebuilt(data):
    _, p, b = _mods()
    b.build_markets()
    b.build_fills()
    sf = data / "lake/polymarket/fills_state.json"
    state = json.loads(sf.read_text())
    state["_sources_retired"] = {"before_month": "2025-02"}
    sf.write_text(json.dumps(state))
    jan = sorted((data / "lake/polymarket/fills/year=2025/month=01").glob("*.parquet"))
    before = [f.stat().st_mtime_ns for f in jan]
    # the originals of January are gone; a rebuild must not replace the lake month with nothing
    (data / "polymarket_clean/trades/part-0000.parquet").unlink()
    b.build_fills(["2025-01"], rebuild=True)
    b.build_fills(rebuild=True)
    assert [f.stat().st_mtime_ns for f in jan] == before
    assert json.loads(sf.read_text())["2025-01"]["rows"] == 6
