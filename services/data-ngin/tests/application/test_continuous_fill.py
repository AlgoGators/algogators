"""The continuous fill (data_ngin.application.continuous_fill): the contract rule, the holes it
finds, the one-transaction insert, and its task in dags/new_data_pipeline_dag.py."""

import importlib.util
import sys
import types
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from data_ngin.application import continuous_fill as cf
from data_ngin.application import pipeline_tasks

DAG = Path(__file__).resolve().parents[2] / "dags" / "new_data_pipeline_dag.py"


def bar(root, day, iid, volume, expiration=date(2026, 12, 1), close=1.0, raw=None):
    return cf.ContractBar(
        root, day, iid, raw or f"{root}{iid}", expiration, close, close + 1, close - 1, close, volume
    )


def by_day(*bars):
    out = {}
    for b in bars:
        out.setdefault(b.day, {})[b.instrument_id] = b
    return out


THU, FRI, SAT, SUN, MON, TUE, WED = (date(2026, 5, d) for d in (14, 15, 16, 17, 18, 19, 20))


class TestBusiest:
    def test_volume_first(self):
        assert cf.busiest([bar("CL", MON, "1", 5), bar("CL", MON, "2", 9)]).instrument_id == "2"

    def test_tie_takes_the_earlier_expiration_then_the_lower_id(self):
        early = bar("CL", MON, "9", 5, expiration=date(2026, 6, 1))
        late = bar("CL", MON, "1", 5, expiration=date(2026, 7, 1))
        assert cf.busiest([late, early]).instrument_id == "9"
        a = bar("CL", MON, "7", 5, expiration=None)
        b = bar("CL", MON, "8", 5, expiration=None)
        assert cf.busiest([b, a]).instrument_id == "7"
        assert cf.busiest([a, bar("CL", MON, "8", 5)]).instrument_id == "8"


class TestVolumeRule:
    def test_weekday_takes_the_previous_weekdays_busiest(self):
        days = by_day(bar("CL", FRI, "old", 9), bar("CL", FRI, "new", 5),
                      bar("CL", MON, "old", 1), bar("CL", MON, "new", 50))
        b, branch = cf.volume_pick(days, MON)
        assert (b.instrument_id, branch) == ("old", cf.PREV_WEEKDAY)
        assert b.day == MON

    def test_monday_reads_friday_not_sunday(self):
        days = by_day(bar("CL", FRI, "old", 9), bar("CL", FRI, "new", 5),
                      bar("CL", SUN, "new", 900), bar("CL", MON, "old", 1), bar("CL", MON, "new", 2))
        assert cf.volume_pick(days, MON)[0].instrument_id == "old"

    def test_fallback_when_the_previous_busiest_has_no_bar(self):
        days = by_day(bar("6L", FRI, "a", 9), bar("6L", MON, "b", 1), bar("6L", MON, "c", 3))
        b, branch = cf.volume_pick(days, MON)
        assert (b.instrument_id, branch) == ("c", cf.FALLBACK)

    def test_first_date(self):
        b, branch = cf.volume_pick(by_day(bar("6L", MON, "a", 1)), MON)
        assert (b.instrument_id, branch) == ("a", cf.FIRST_DATE)

    def test_no_bar_on_the_date(self):
        assert cf.volume_pick(by_day(bar("6L", FRI, "a", 1)), MON) == (None, "")

    def test_sunday_keeps_the_previous_weekdays_pick(self):
        # CL 2026-05-17 (Databento: 670557 to Sunday, 777566 from Monday): Thursday's busiest
        # is still Friday's pick, though Friday's own busiest is the next contract.
        days = by_day(bar("CL", THU, "670557", 9), bar("CL", THU, "777566", 8),
                      bar("CL", FRI, "670557", 7), bar("CL", FRI, "777566", 8),
                      bar("CL", SUN, "670557", 1), bar("CL", SUN, "777566", 3))
        b, branch = cf.volume_pick(days, SUN)
        assert (b.instrument_id, branch) == ("670557", cf.WEEKEND)
        days[MON] = {"777566": bar("CL", MON, "777566", 9), "670557": bar("CL", MON, "670557", 1)}
        assert cf.volume_pick(days, MON)[0].instrument_id == "777566"

    def test_sunday_falls_back_when_the_pick_has_no_bar(self):
        days = by_day(bar("6M", THU, "a", 9), bar("6M", FRI, "a", 9), bar("6M", SUN, "b", 2))
        b, branch = cf.volume_pick(days, SUN)
        assert (b.instrument_id, branch) == ("b", cf.FALLBACK)


class TestPickContract:
    def test_bracket_when_both_neighbours_agree(self):
        days = by_day(bar("SI", FRI, "a", 1), bar("SI", MON, "a", 1), bar("SI", MON, "b", 99))
        b, branch = cf.pick_contract(days, MON, "a", "a")
        assert (b.instrument_id, branch) == ("a", cf.BRACKET)

    def test_carry_the_earlier_neighbour_when_the_neighbours_differ_or_no_later_row(self):
        # 6L 2012-08-20/21: the volume rule's busiest (here "b") would show the roll rule a
        # vendor switch the vendor never made; the earlier row's contract keeps it on the next row
        days = by_day(bar("SI", FRI, "b", 9), bar("SI", MON, "a", 1), bar("SI", MON, "b", 2))
        assert cf.pick_contract(days, MON, "a", "b")[0].instrument_id == "a"
        assert cf.pick_contract(days, MON, "a", "b")[1] == cf.CARRY
        assert cf.pick_contract(days, MON, "a", None)[1] == cf.CARRY

    def test_volume_rule_when_the_earlier_contract_has_no_bar(self):
        days = by_day(bar("SI", FRI, "b", 9), bar("SI", MON, "b", 2))
        assert cf.pick_contract(days, MON, "a", "a") == (days[MON]["b"], cf.PREV_WEEKDAY)
        assert cf.pick_contract(days, MON, "a", "b")[1] == cf.PREV_WEEKDAY
        assert cf.pick_contract(days, MON, None, None)[1] == cf.PREV_WEEKDAY


def vrow(day, iid, rtype="35", publisher="1"):
    return cf.VendorRow(day, iid, rtype if iid else None, publisher if iid else None)


class TestFindFills:
    def test_fills_a_weekday_hole_and_a_sunday_never_a_saturday(self):
        bars = [bar("SI", d, "a", 10) for d in (THU, FRI, SAT, SUN, MON)]
        rows = {"SI.v.0": [vrow(THU, "a"), vrow(MON, "a")]}
        fills = cf.find_fills(rows, bars, THU, MON)
        assert [(f.day, f.bar.instrument_id, f.branch) for f in fills] == [
            (FRI, "a", cf.BRACKET),
            (SUN, "a", cf.BRACKET),
        ]
        assert (fills[0].rtype, fills[0].publisher_id) == ("35", "1")

    def test_only_inside_the_window(self):
        bars = [bar("SI", d, "a", 10) for d in (THU, FRI, MON, TUE)]
        rows = {"SI.v.0": [vrow(THU, "a"), vrow(TUE, "a")]}
        assert [f.day for f in cf.find_fills(rows, bars, MON, MON)] == [MON]

    def test_never_the_newest_day(self):
        # audit A S-6 (b): with no later real row the pulls' D-5..D re-fetch owns the day; a fill
        # there could carry a one-day vendor stray of D-1 into a confirmed switch on a live book
        bars = [bar("SI", d, "a", 10) for d in (THU, FRI, MON)]
        rows = {"SI.v.0": [vrow(THU, "a"), vrow(FRI, "a")]}
        skipped = []
        assert cf.find_fills(rows, bars, THU, MON, skipped=skipped) == []
        assert skipped == [("SI.v.0", MON, "no later real row yet: left to the pulls")]

    def test_a_partial_contract_load_is_skipped(self):
        # audit A S-6 (a): never pick from a root whose contract load for the date is incomplete
        bars = [bar("CL", d, "z6", 10) for d in (THU, MON)] + [bar("CL", FRI, "f7", 3)]
        rows = {"CL.v.0": [vrow(THU, "z6"), vrow(MON, "z6")]}
        skipped = []
        fills = cf.find_fills(rows, bars, THU, MON, lambda root, day: day != FRI, skipped)
        assert fills == [] and skipped == [("CL.v.0", FRI, "the contract load of CL is not complete")]

    def test_no_fill_before_the_first_row_or_without_a_contract_bar(self):
        bars = [bar("SI", d, "a", 10) for d in (THU, FRI)]
        rows = {"SI.v.0": [vrow(FRI, "a")], "GC.v.0": [vrow(THU, None)]}
        assert cf.find_fills(rows, bars, THU, MON) == []

    def test_the_source_root_follows_the_nearest_earlier_row(self):
        # MES.v.0 carried ES bars before its own contracts (trade-ngin's grid convention)
        bars = [bar("ES", THU, "es", 10), bar("ES", FRI, "es", 10), bar("MES", FRI, "mes", 5),
                bar("ES", MON, "es", 10), bar("MES", MON, "mes", 5), bar("MES", TUE, "mes", 5),
                bar("MES", WED, "mes", 5)]
        rows = {"MES.v.0": [vrow(THU, "es"), vrow(MON, "mes"), vrow(WED, "mes")]}
        fills = cf.find_fills(rows, bars, THU, WED)
        assert [(f.day, f.bar.root) for f in fills] == [(FRI, "ES"), (TUE, "MES")]

    def test_an_instrument_id_is_matched_on_its_date(self):
        # the same id names another root's contract in another year: not this symbol's root
        bars = [bar("6A", THU, "77", 10), bar("MES", date(2024, 1, 2), "77", 10),
                bar("6A", FRI, "77", 10), bar("6A", MON, "77", 10)]
        rows = {"6A.v.0": [vrow(THU, "77"), vrow(MON, "77")]}
        assert [f.bar.root for f in cf.find_fills(rows, bars, FRI, FRI)] == ["6A"]


class FakeCursor:
    def __init__(self, vendor, bars, rowcount=1, readiness=None):
        self.vendor, self.bars, self.rowcount_value = vendor, bars, rowcount
        # every date's contract load complete unless a test says otherwise
        days = [THU, FRI, SAT, SUN, MON, TUE, WED]
        self.readiness = [(d, "complete", []) for d in days] if readiness is None else readiness
        self.statements = []
        self._result = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        if sql is cf.READ_VENDOR_ROWS:
            self._result = self.vendor
        elif sql is cf.READ_CONTRACT_BARS:
            self._result = self.bars
        elif sql is getattr(cf, "READ_CONTRACT_READINESS", None):
            self._result = self.readiness
        self.rowcount = self.rowcount_value if sql in (cf.INSERT_CONTINUOUS, cf.INSERT_RAW) else 0

    def fetchall(self):
        return self._result


def contract_row(b):
    return (b.root, b.day, int(b.instrument_id), b.raw_symbol, b.expiration,
            b.open, b.high, b.low, b.close, b.volume)


class TestFillContinuousHoles:
    VENDOR = [("SI.v.0", THU, "1", "35", "1"), ("SI.v.0", MON, "1", "35", "1")]
    BARS = [contract_row(bar("SI", d, "1", 10)) for d in (THU, FRI, MON)]

    def test_one_locked_transaction_inserting_both_tables(self):
        cur = FakeCursor(self.VENDOR, self.BARS)
        conn = MagicMock()
        conn.cursor.return_value = cur
        filled = cf.fill_continuous_holes(conn, THU, MON)
        assert [(f.symbol, f.day, f.branch) for f in filled] == [("SI.v.0", FRI, cf.BRACKET)]
        sql = [s for s, _ in cur.statements]
        assert sql[0] == "BEGIN"
        assert sql[1].startswith("LOCK TABLE futures_data.ohlcv_1d, futures_data.ohlcv_1d_raw")
        assert sql[-1] == "COMMIT"
        inserts = [(s, p) for s, p in cur.statements if s.startswith("INSERT")]
        assert [s.split()[2] for s, _ in inserts] == ["futures_data.ohlcv_1d", "futures_data.ohlcv_1d_raw"]
        p = inserts[1][1]
        assert p["ts"] == datetime(2026, 5, 15, tzinfo=UTC)
        assert p["ts_next"] == datetime(2026, 5, 16, tzinfo=UTC)
        assert (p["instrument_id"], p["volume"], p["close"], p["rtype"], p["publisher_id"]) == (
            "1", 10, 1.0, "35", "1")
        assert all("NOT EXISTS" in s for s, _ in inserts)

    def test_a_volume_rule_day_writes_no_vendor_id_to_the_raw_row(self):
        # no earlier row names a contract with a bar on FRI: the volume rule, raw instrument_id NULL
        vendor = [("6L.v.0", THU, "9", "35", "1"), ("6L.v.0", MON, "8", "35", "1")]
        bars = [contract_row(bar("6L", THU, "9", 5)), contract_row(bar("6L", FRI, "1", 3)),
                contract_row(bar("6L", MON, "8", 4))]
        cur = FakeCursor(vendor, bars)
        conn = MagicMock()
        conn.cursor.return_value = cur
        filled = cf.fill_continuous_holes(conn, THU, MON)
        assert [(f.day, f.bar.instrument_id, f.branch) for f in filled] == [(FRI, "1", cf.FALLBACK)]
        raw = [p for s, p in cur.statements if s.startswith("INSERT INTO futures_data.ohlcv_1d_raw")]
        assert raw[0]["instrument_id"] is None and raw[0]["close"] == 1.0

    def test_a_root_missing_from_the_contract_readiness_is_not_filled(self):
        for readiness in ([(FRI, "partial", ["SI"])], [(FRI, "failed", [])], []):
            cur = FakeCursor(self.VENDOR, self.BARS, readiness=readiness)
            conn = MagicMock()
            conn.cursor.return_value = cur
            assert cf.fill_continuous_holes(conn, THU, MON) == [], readiness
            assert not any(s.startswith("INSERT") for s, _ in cur.statements)
            assert cur.statements[-1][0] == "COMMIT"

    def test_nothing_missing_inserts_nothing(self):
        vendor = [("SI.v.0", d, "1", "35", "1") for d in (THU, FRI, MON)]
        cur = FakeCursor(vendor, self.BARS)
        conn = MagicMock()
        conn.cursor.return_value = cur
        assert cf.fill_continuous_holes(conn, THU, MON) == []
        assert not any(s.startswith("INSERT") for s, _ in cur.statements)
        assert cur.statements[-1][0] == "COMMIT"

    def test_a_row_not_inserted_rolls_back_and_raises(self):
        cur = FakeCursor(self.VENDOR, self.BARS, rowcount=0)
        conn = MagicMock()
        conn.cursor.return_value = cur
        with pytest.raises(RuntimeError, match="expected 1 / 1"):
            cf.fill_continuous_holes(conn, THU, MON)
        assert cur.statements[-1][0] == "ROLLBACK"

    def test_each_inserted_row_is_logged(self, caplog):
        cur = FakeCursor(self.VENDOR, self.BARS)
        conn = MagicMock()
        conn.cursor.return_value = cur
        with caplog.at_level("WARNING"):
            cf.fill_continuous_holes(conn, THU, MON)
        assert "CONTINUOUS_FILL SI.v.0 2026-05-15 from SI1 (instrument_id 1, B_bracket, volume 10)" in caplog.text


def test_run_continuous_fill_reads_the_lookback_and_summarises():
    repo = MagicMock()
    filled = [cf.FilledRow("SI.v.0", FRI, bar("SI", FRI, "1", 10), cf.BRACKET, "35", "1")]
    with (
        patch("data_ngin.infrastructure.repository.ohlcv_repository.OhlcvRepository", return_value=repo),
        patch.object(cf, "fill_continuous_holes", return_value=filled) as fill,
    ):
        out = cf.run_continuous_fill({}, MON)
    fill.assert_called_once_with(repo.connection, date(2026, 5, 8), MON)
    repo.close.assert_called_once()
    assert out == {"bar_date": "2026-05-18", "filled": [
        {"symbol": "SI.v.0", "date": "2026-05-15", "instrument_id": "1", "raw_symbol": "SI1",
         "branch": cf.BRACKET}]}


def test_pipeline_task_runs_the_fill_for_the_bar_date():
    with (
        patch("data_ngin.utils.dynamic_loader.load_config", return_value={"k": 1}),
        patch("data_ngin.application.continuous_fill.run_continuous_fill", return_value={"x": 1}) as fill,
    ):
        assert pipeline_tasks.run_continuous_fill("new_config.yaml", MON) == {"x": 1}
    fill.assert_called_once_with({"k": 1}, MON)


def test_dag_fill_task_calls_the_pipeline_task():
    sdk = types.ModuleType("airflow.sdk")
    sdk.task = lambda f: f
    sdk.dag = lambda **_kw: lambda _f: lambda: None
    sdk.get_current_context = lambda: {}
    airflow = types.ModuleType("airflow")
    airflow.sdk = sdk
    pendulum = types.ModuleType("pendulum")
    pendulum.timezone = lambda _name: None
    with patch.dict(sys.modules, {"airflow": airflow, "airflow.sdk": sdk, "pendulum": pendulum}):
        spec = importlib.util.spec_from_file_location("new_data_pipeline_dag_fill", DAG)
        dag = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dag)
    with (
        patch.object(pipeline_tasks, "bar_date_from_context", return_value=MON),
        patch.object(pipeline_tasks, "run_continuous_fill", return_value={"filled": []}) as fill,
    ):
        assert dag.fill_continuous_holes({"status": "complete"}) == {"filled": []}
    fill.assert_called_once_with("new_config.yaml", MON)
    text = DAG.read_text()
    assert "fill_result = fill_continuous_holes(contract_result)" in text
    # audit A S-7: readiness is published only after the fill has committed
    assert "fill_result >> [write_readiness(pipeline_result), write_contract_readiness(contract_result)]" in text
    assert text.count("write_readiness(pipeline_result)") == 1
