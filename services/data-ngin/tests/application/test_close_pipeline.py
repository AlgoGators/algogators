"""After-close futures pipeline: bar-date derivation, gap backfill, readiness."""

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from data_ngin.application import close_pipeline, pipeline_tasks
from data_ngin.application.close_pipeline import (
    availability_wait_seconds,
    backfill_days,
    bar_date_for_run,
    find_missing_bars,
    load_contracts,
    readiness_row,
    repull_ranges,
    run_close_pipeline,
    should_retry_for_missing,
    storage_symbol,
    wait_for_bar_date,
    write_readiness,
)
from data_ngin.utils.dynamic_loader import load_config

NY = ZoneInfo("America/New_York")
CONFIG = load_config(pipeline_tasks.config_path("new_config.yaml"))

FX = {"dataSymbol": "6E", "instrumentType": "FUTURE", "exchange": "CME", "underlying": "FX"}
ES = {"dataSymbol": "ES", "instrumentType": "FUTURE", "exchange": "CME", "underlying": "Equity"}
ZC = {"dataSymbol": "ZC", "instrumentType": "FUTURE", "exchange": "CBOT", "underlying": "Ags"}


def to_storage(symbol: str) -> str:
    return storage_symbol(CONFIG, symbol)


class TestBarDate(unittest.TestCase):
    def test_20_et_in_edt_pulls_that_new_york_date(self) -> None:
        run = datetime(2026, 10, 12, 20, 0, tzinfo=NY)  # 00:00 UTC Oct 13
        self.assertEqual(bar_date_for_run(run), date(2026, 10, 12))

    def test_20_et_in_est_pulls_that_new_york_date(self) -> None:
        run = datetime(2026, 1, 12, 20, 0, tzinfo=NY)  # 01:00 UTC Jan 13
        self.assertEqual(bar_date_for_run(run), date(2026, 1, 12))

    def test_sunday_run_pulls_sunday(self) -> None:
        self.assertEqual(
            bar_date_for_run(datetime(2026, 10, 11, 20, 0, tzinfo=NY)), date(2026, 10, 11)
        )

    def test_earlier_run_never_pulls_an_unfinished_utc_day(self) -> None:
        run = datetime(2026, 10, 12, 19, 59, tzinfo=NY)  # 23:59 UTC Oct 12
        self.assertEqual(bar_date_for_run(run), date(2026, 10, 11))

    def test_naive_reference_is_utc(self) -> None:
        self.assertEqual(bar_date_for_run(datetime(2026, 10, 13, 0, 0)), date(2026, 10, 12))

    def test_context_uses_data_interval_end(self) -> None:
        context = {
            "data_interval_end": datetime(2026, 1, 12, 20, 0, tzinfo=NY),
            "logical_date": datetime(2026, 1, 11, 20, 0, tzinfo=NY),
            "dag_run": SimpleNamespace(conf={}, run_id="scheduled__x"),
        }
        self.assertEqual(pipeline_tasks.bar_date_from_context(context), date(2026, 1, 12))
        self.assertEqual(pipeline_tasks.run_id_from_context(context), "scheduled__x")

    def test_context_conf_pins_a_past_date(self) -> None:
        context = {"dag_run": SimpleNamespace(conf={"bar_date": "2026-10-01"})}
        self.assertEqual(pipeline_tasks.bar_date_from_context(context), date(2026, 10, 1))

    def test_context_conf_rejects_an_unfinished_day(self) -> None:
        tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        context = {"dag_run": SimpleNamespace(conf={"bar_date": tomorrow})}
        with self.assertRaises(ValueError):
            pipeline_tasks.bar_date_from_context(context)

    def test_context_without_any_time_uses_latest_complete_day(self) -> None:
        expected = bar_date_for_run(datetime.now(UTC))
        self.assertEqual(pipeline_tasks.bar_date_from_context({}), expected)


class TestBackfill(unittest.TestCase):
    def test_window_is_five_days_before_plus_d(self) -> None:
        days = backfill_days(date(2026, 10, 12))
        self.assertEqual(days[0], date(2026, 10, 7))
        self.assertEqual(days[-1], date(2026, 10, 12))
        self.assertEqual(len(days), 6)

    def test_picks_only_missing_expected_pairs(self) -> None:
        d1, d2, d3 = date(2026, 10, 9), date(2026, 10, 11), date(2026, 10, 12)
        expected = {d1: [FX, ES, ZC], d2: [FX, ES], d3: [FX, ES, ZC]}
        present = {
            ("6E.v.0", d1),
            ("MES.v.0", d1),
            ("ZC.v.0", d1),
            ("6E.v.0", d2),
            # MES Sunday bar missing; nothing for D yet.
        }
        missing = find_missing_bars(expected, present, to_storage)
        self.assertEqual(missing, {"6E": [d3], "ES": [d2, d3], "ZC": [d3]})

    def test_nothing_missing(self) -> None:
        d = date(2026, 10, 12)
        self.assertEqual(find_missing_bars({d: [FX]}, {("6E.v.0", d)}, to_storage), {})


class TestReadiness(unittest.TestCase):
    D = date(2026, 10, 12)

    def test_complete(self) -> None:
        row = readiness_row(["6E.v.0", "MES.v.0"], {"6E.v.0", "MES.v.0"}, self.D, "r1")
        self.assertEqual(row["status"], "complete")
        self.assertEqual((row["expected"], row["loaded"], row["missing"]), (2, 2, []))
        self.assertEqual(row["dataset"], "futures_ohlcv_1d")
        self.assertEqual(row["run_id"], "r1")

    def test_partial_lists_missing(self) -> None:
        row = readiness_row(["6E.v.0", "MES.v.0", "ZC.v.0"], {"6E.v.0"}, self.D, "r1")
        self.assertEqual(row["status"], "partial")
        self.assertEqual((row["expected"], row["loaded"]), (3, 1))
        self.assertEqual(row["missing"], ["MES.v.0", "ZC.v.0"])

    def test_failed_pull(self) -> None:
        row = readiness_row(["6E.v.0"], set(), self.D, "r1", failed=True)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["missing"], ["6E.v.0"])

    def test_failed_pull_with_every_bar_present_is_complete(self) -> None:
        row = readiness_row(["6E.v.0"], {"6E.v.0"}, self.D, "r1", failed=True)
        self.assertEqual(row["status"], "complete")

    @patch("data_ngin.infrastructure.repository.readiness_repository.ReadinessRepository")
    @patch.object(close_pipeline, "_present_bars")
    def test_write_upserts_row(self, mock_present: MagicMock, mock_repo_cls: MagicMock) -> None:
        mock_present.return_value = {("6E.v.0", self.D)}
        with patch.object(close_pipeline, "load_contracts", return_value=[FX, ES]):
            row = write_readiness(CONFIG, self.D, "r1")
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["missing"], ["MES.v.0"])
        mock_repo_cls.return_value.upsert_readiness.assert_called_once_with(row)
        mock_repo_cls.return_value.close.assert_called_once()

    @patch("data_ngin.infrastructure.repository.readiness_repository.ReadinessRepository")
    def test_write_nothing_when_nothing_traded(self, mock_repo_cls: MagicMock) -> None:
        with patch.object(close_pipeline, "load_contracts", return_value=[FX, ES]):
            self.assertIsNone(write_readiness(CONFIG, date(2026, 10, 10), "r1"))  # Saturday
        mock_repo_cls.assert_not_called()

    def test_retry_while_retries_remain(self) -> None:
        self.assertTrue(should_retry_for_missing(["6E.v.0"], try_number=1, max_tries=3))
        self.assertTrue(should_retry_for_missing(["6E.v.0"], try_number=3, max_tries=3))
        self.assertFalse(should_retry_for_missing(["6E.v.0"], try_number=4, max_tries=3))
        self.assertFalse(should_retry_for_missing([], try_number=1, max_tries=3))
        # Databento had not published D: waiting again would not help.
        self.assertFalse(should_retry_for_missing(["6E.v.0"], 1, 3, published=False))


class TestRunClosePipeline(unittest.TestCase):
    D = date(2026, 10, 12)  # Monday

    def test_saturday_skips_the_pull(self) -> None:
        with (
            patch.object(close_pipeline, "load_contracts", return_value=[FX, ES, ZC]),
            patch.object(close_pipeline, "_present_bars") as mock_present,
            patch("data_ngin.application.orchestrator.Orchestrator") as mock_orch,
        ):
            result = run_close_pipeline(CONFIG, date(2026, 10, 10))
        self.assertTrue(result["skipped"])
        mock_present.assert_not_called()
        mock_orch.assert_not_called()

    def _run(self, published_through: date) -> tuple[dict, MagicMock]:
        # Present: everything for Oct 7-9 and the Sunday FX bar. Missing: the
        # Sunday MES bar (backfill) and all of D.
        present = {
            (s, d)
            for s in ("6E.v.0", "MES.v.0", "ZC.v.0")
            for d in (date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9))
        } | {("6E.v.0", date(2026, 10, 11))}
        after = {("6E.v.0", self.D), ("MES.v.0", self.D)}

        async def _process(jobs: list, update_existing: bool = False) -> None:
            return None

        dataset_end = datetime.combine(published_through, datetime.min.time(), tzinfo=UTC)
        with (
            patch.object(close_pipeline, "load_contracts", return_value=[FX, ES, ZC]),
            patch.object(close_pipeline, "_present_bars", side_effect=[present, after]),
            patch.object(close_pipeline, "availability_wait_seconds", return_value=0),
            patch("data_ngin.application.orchestrator.Orchestrator") as mock_orch,
        ):
            mock_orch.return_value.fetcher.daily_availability.return_value = (
                published_through,
                dataset_end + timedelta(hours=16),
            )
            mock_orch.return_value.process.side_effect = _process
            result = run_close_pipeline(CONFIG, self.D)
        return result, mock_orch.return_value.process

    def test_repulls_the_whole_window_with_updates(self) -> None:
        result, process = self._run(published_through=self.D)

        jobs = process.call_args.args[0]
        ranges = {job[0]["dataSymbol"]: (job[1], job[2]) for job in jobs}
        self.assertEqual(
            ranges,
            {
                "6E": ("2026-10-07", "2026-10-12"),
                "ES": ("2026-10-07", "2026-10-12"),
                "ZC": ("2026-10-07", "2026-10-12"),
            },
        )
        self.assertTrue(process.call_args.kwargs["update_existing"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["expected"], ["6E.v.0", "MES.v.0", "ZC.v.0"])
        self.assertEqual(result["missing"], ["ZC.v.0"])
        self.assertEqual(result["published_through"], "2026-10-12")

    def test_never_requests_past_what_databento_published(self) -> None:
        result, process = self._run(published_through=date(2026, 10, 11))

        ranges = {job[0]["dataSymbol"]: job[2] for job in process.call_args.args[0]}
        self.assertEqual(set(ranges.values()), {"2026-10-11"})
        self.assertEqual(result["published_through"], "2026-10-11")
        self.assertFalse(pipeline_tasks.should_retry_run(result, try_number=1, max_tries=3))


class TestWaitForBarDate(unittest.TestCase):
    D = date(2026, 10, 12)
    MIDNIGHT = datetime(2026, 10, 13, tzinfo=UTC)

    def test_returns_at_once_when_published(self) -> None:
        sleep = MagicMock()
        through = wait_for_bar_date(
            lambda: (self.D, self.MIDNIGHT), self.D, 3600, sleep=sleep, now=lambda: self.MIDNIGHT
        )
        self.assertEqual(through, self.D)
        sleep.assert_not_called()

    def test_polls_until_published(self) -> None:
        clock = [self.MIDNIGHT]
        states = iter(
            [
                (self.D - timedelta(days=1), self.MIDNIGHT - timedelta(minutes=10)),
                (self.D, self.MIDNIGHT + timedelta(minutes=1)),
            ]
        )

        def sleep(seconds: float) -> None:
            clock[0] += timedelta(seconds=seconds)

        through = wait_for_bar_date(
            lambda: next(states), self.D, 3600, poll_seconds=300, now=lambda: clock[0], sleep=sleep
        )
        self.assertEqual(through, self.D)
        self.assertEqual(clock[0], self.MIDNIGHT + timedelta(minutes=5))

    def test_gives_up_at_once_when_the_delay_exceeds_the_budget(self) -> None:
        # The 8-hour historical delay: at 00:00 UTC data reaches only 16:00.
        sleep = MagicMock()
        with self.assertLogs("data_ngin.application.close_pipeline", "ERROR"):
            through = wait_for_bar_date(
                lambda: (self.D - timedelta(days=1), self.MIDNIGHT - timedelta(hours=8)),
                self.D,
                3600,
                now=lambda: self.MIDNIGHT,
                sleep=sleep,
            )
        self.assertEqual(through, self.D - timedelta(days=1))
        sleep.assert_not_called()

    def test_wait_budget_from_env(self) -> None:
        with patch.dict(os.environ, {"DATABENTO_AVAILABILITY_WAIT_MINUTES": "5"}):
            self.assertEqual(availability_wait_seconds(), 300)
        with patch.dict(os.environ, {"DATABENTO_AVAILABILITY_WAIT_MINUTES": ""}):
            self.assertEqual(availability_wait_seconds(), 3600)

    def test_repull_ranges(self) -> None:
        d1, d2 = date(2026, 10, 9), date(2026, 10, 12)
        self.assertEqual(repull_ranges({d1: [FX, ZC], d2: [FX]}), {"6E": (d1, d2), "ZC": (d1, d1)})


class TestLoadContracts(unittest.TestCase):
    def test_packaged_csvs_carry_exchange_and_underlying(self) -> None:
        for name in ("config.yaml", "new_config.yaml"):
            with self.subTest(config=name):
                contracts = load_contracts(load_config(pipeline_tasks.config_path(name)))
                self.assertGreater(len(contracts), 0)
                for contract in contracts:
                    self.assertTrue(contract["exchange"], contract)
                    self.assertTrue(contract["underlying"], contract)

    def test_every_packaged_contract_maps_to_a_calendar(self) -> None:
        from data_ngin.application.trading_calendar import calendar_name_for

        for name in ("config.yaml", "new_config.yaml"):
            contracts = load_contracts(load_config(pipeline_tasks.config_path(name)))
            unmapped = [c["dataSymbol"] for c in contracts if calendar_name_for(c) is None]
            self.assertEqual(unmapped, [], name)

    def test_storage_symbol_applies_remap(self) -> None:
        self.assertEqual(storage_symbol(CONFIG, "ES"), "MES.v.0")
        self.assertEqual(storage_symbol(CONFIG, "6E"), "6E.v.0")

    def test_full_contract_list(self) -> None:
        with patch.dict(os.environ, {"DATA_NGIN_CONTRACTS_DIR": ""}):
            self.assertEqual(len(load_contracts(CONFIG)), 36)


if __name__ == "__main__":
    unittest.main()
