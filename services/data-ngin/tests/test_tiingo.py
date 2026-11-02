import os
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
from data_ngin.infrastructure.cleaner.tiingo_cleaner import TiingoCleaner
from data_ngin.infrastructure.fetcher.tiingo_fetcher import OUTPUT_COLUMNS, TiingoFetcher

KEYS_ENV = {"TIINGO_API_KEY_JN1": "key-b", "TIINGO_API_KEY_DD": "key-a", "TIINGO_API_KEY_XR1": " "}


def _tiingo_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "date": "2026-01-02T00:00:00.000Z",
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": 1000,
        "adjOpen": 10.0,
        "adjHigh": 11.0,
        "adjLow": 9.5,
        "adjClose": 10.5,
        "adjVolume": 1000.0,
        "divCash": 0.0,
        "splitFactor": 1.0,
    }
    row.update(overrides)
    return row


class _FakeResponse:
    def __init__(self, status: int, payload: Any = None) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return f"status {self.status}"

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeSession:
    """Stands in for aiohttp.ClientSession; replies from a shared queue."""

    def __init__(self, responses: list[_FakeResponse], calls: list[dict]) -> None:
        self._responses = responses
        self._calls = calls

    def get(self, url: str, params: dict) -> _FakeResponse:
        self._calls.append({"url": url, **params})
        return self._responses.pop(0)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


@patch.dict(os.environ, KEYS_ENV)
class TestTiingoFetcher(unittest.IsolatedAsyncioTestCase):
    def _fetcher_with(self, responses: list[_FakeResponse]) -> tuple[TiingoFetcher, list[dict]]:
        calls: list[dict] = []
        patcher = patch(
            "data_ngin.infrastructure.fetcher.tiingo_fetcher.aiohttp.ClientSession",
            side_effect=lambda **_kw: _FakeSession(responses, calls),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return TiingoFetcher(config={"provider": {"asset": "EQUITY"}}), calls

    def test_collects_non_blank_keys_in_name_order(self) -> None:
        fetcher = TiingoFetcher(config={})
        self.assertEqual(fetcher.api_keys, ["key-a", "key-b"])

    def test_no_keys_raises(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("TIINGO_API_KEY")}
        with patch.dict(os.environ, env, clear=True), self.assertRaises(OSError):
            TiingoFetcher(config={})

    def test_primary_key_is_stable(self) -> None:
        fetcher = TiingoFetcher(config={})
        self.assertEqual(fetcher._primary_index("AAPL"), fetcher._primary_index("AAPL"))

    async def test_success_maps_columns(self) -> None:
        fetcher, calls = self._fetcher_with([_FakeResponse(200, [_tiingo_row()])])
        df = await fetcher.fetch_data("AAPL", "EQUITY", "2026-01-02", "2026-01-02")

        self.assertEqual(list(df.columns), OUTPUT_COLUMNS)
        self.assertEqual(df.loc[0, "symbol"], "AAPL")
        self.assertEqual(df.loc[0, "adjusted_close"], 10.5)
        self.assertEqual(calls[0]["startDate"], "2026-01-02")
        self.assertEqual(calls[0]["endDate"], "2026-01-02")

    async def test_rotates_key_on_rate_limit(self) -> None:
        fetcher, calls = self._fetcher_with(
            [_FakeResponse(429), _FakeResponse(200, [_tiingo_row()])]
        )
        df = await fetcher.fetch_data("AAPL", "EQUITY", "2026-01-02", "2026-01-02")

        self.assertEqual(len(df), 1)
        self.assertNotEqual(calls[0]["token"], calls[1]["token"])
        self.assertEqual(len(fetcher._disabled_keys), 1)

    async def test_all_keys_exhausted_raises(self) -> None:
        fetcher, _calls = self._fetcher_with([_FakeResponse(429), _FakeResponse(403)])
        with self.assertRaisesRegex(RuntimeError, "exhausted"):
            await fetcher.fetch_data("AAPL", "EQUITY", "2026-01-02", "2026-01-02")

    async def test_server_error_does_not_burn_key(self) -> None:
        fetcher, _calls = self._fetcher_with([_FakeResponse(500)])
        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            await fetcher.fetch_data("AAPL", "EQUITY", "2026-01-02", "2026-01-02")
        self.assertEqual(fetcher._disabled_keys, set())

    async def test_empty_response_returns_empty_frame(self) -> None:
        fetcher, _calls = self._fetcher_with([_FakeResponse(200, [])])
        df = await fetcher.fetch_data("AAPL", "EQUITY", "2026-01-02", "2026-01-02")
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), OUTPUT_COLUMNS)

    async def test_missing_fields_raise(self) -> None:
        fetcher, _calls = self._fetcher_with([_FakeResponse(200, [{"date": "2026-01-02"}])])
        with self.assertRaisesRegex(RuntimeError, "missing expected fields"):
            await fetcher.fetch_data("AAPL", "EQUITY", "2026-01-02", "2026-01-02")


class TestTiingoCleaner(unittest.TestCase):
    def _raw(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        fetcher = MagicMock(spec=TiingoFetcher)
        return TiingoFetcher._to_dataframe(fetcher, rows, "aapl ")

    def test_clean_types_and_order(self) -> None:
        raw = self._raw(
            [_tiingo_row(date="2026-01-05T00:00:00.000Z"), _tiingo_row(), _tiingo_row()]
        )
        rows = TiingoCleaner(config={}).clean(raw)

        self.assertEqual(len(rows), 2)  # duplicate (symbol, time) dropped
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertLess(rows[0]["time"], rows[1]["time"])
        self.assertEqual(str(rows[0]["time"].tz), "UTC")
        self.assertIsInstance(rows[0]["volume"], int)

    def test_drops_corrupt_rows(self) -> None:
        raw = self._raw(
            [
                _tiingo_row(),
                _tiingo_row(date="2026-01-05", close=0),
                _tiingo_row(date="2026-01-06", volume=-1),
            ]
        )
        rows = TiingoCleaner(config={}).clean(raw)
        self.assertEqual(len(rows), 1)

    def test_drop_nan_config(self) -> None:
        raw = self._raw([_tiingo_row(), _tiingo_row(date="2026-01-05", divCash=None)])
        rows = TiingoCleaner(config={"missing_data": {"drop_nan": True}}).clean(raw)
        self.assertEqual(len(rows), 1)

    def test_empty_input(self) -> None:
        self.assertEqual(TiingoCleaner().clean(pd.DataFrame()), [])

    def test_missing_required_field(self) -> None:
        with self.assertRaises(ValueError):
            TiingoCleaner().clean(pd.DataFrame({"time": ["2026-01-02"]}))


if __name__ == "__main__":
    unittest.main()
