import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import databento as db
import pandas as pd
from data_ngin.domain.services import SymbolRemapper
from data_ngin.infrastructure.fetcher.fetcher import Fetcher
from databento.common.error import BentoServerError

# Databento's historical gateway intermittently answers 504 when many range
# requests arrive at once (seen on 2026-10-08/09: 1-3 of 29-36 symbols per run).
# Capping in-flight requests and retrying 5xx/timeouts per symbol keeps one bad
# response from failing the whole pipeline task, whose retry would refetch
# every symbol.
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_SECONDS = 5.0
RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (BentoServerError, asyncio.TimeoutError)


class DatabentoFetcher(Fetcher):
    """
    A Fetcher subclass for retrieving raw data from Databento's API.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """
        Initializes the DatabentoFetcher with API connection settings and configurations.

        Args:
            config (Dict[str, Any]): Configuration settings.
        """
        super().__init__(config)
        api_key: str = os.getenv("DATABENTO_API_KEY")
        self.client: db.Historical = db.Historical(api_key)
        self.logger: logging.Logger = logging.getLogger("DatabentoFetcher")
        self.logger.setLevel(logging.INFO)
        self.symbol_remapper: SymbolRemapper = SymbolRemapper(config.get("symbol_remap"))
        self._max_concurrency = _env_int("DATABENTO_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY)
        self._max_attempts = _env_int("DATABENTO_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
        self._backoff_seconds = DEFAULT_BACKOFF_SECONDS
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        # Created lazily so it binds to the running event loop. Safe without a
        # lock: there is no await between the check and the assignment.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrency)
        return self._semaphore

    async def _get_range(self, symbol: str, **request: Any) -> Any:
        """
        One Databento range request, at most `_max_concurrency` in flight, retried
        with exponential backoff (5s, 15s, 45s by default) on 5xx responses and
        timeouts. 4xx errors (bad symbol, auth) are not retried.
        """
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with self._get_semaphore():
                    return await self.client.timeseries.get_range_async(**request)
            except RETRYABLE_ERRORS as e:
                if attempt == self._max_attempts:
                    raise
                delay = self._backoff_seconds * 3 ** (attempt - 1)
                self.logger.warning(
                    f"Databento request for {symbol} failed (attempt {attempt}/"
                    f"{self._max_attempts}): {e}; retrying in {delay:.0f}s"
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def fetch_data(
        self,
        symbol: str,
        loaded_asset_type: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        Asynchronously fetches historical data based on asset type and dataset settings.

        Args:
            symbol (str): The symbol to fetch data for.
            loaded_asset_type (str): Type of asset to load (e.g., "FUTURE")
            start_date (str): Start date for fetching.
            end_date (str): End date for fetching.

        Returns:
            pd.DataFrame: Retrieved data as a pandas DataFrame.

        Raises:
            ValueError: If asset type doesn't match the configuration or an unsupported asset type is provided.
            Exception: If an error occurs during data retrieval.
        """
        schema: str = self.config["provider"]["schema"]
        dataset: str = self.config["provider"]["dataset"]
        asset_type_config: str = self.config["provider"]["asset"]

        # Check asset type
        if loaded_asset_type != asset_type_config:
            raise ValueError(f"Asset type mismatch: {loaded_asset_type} != {asset_type_config}")

        if loaded_asset_type == "FUTURE":
            roll_type: str = self.config["provider"]["roll_type"]
            contract_type: str = self.config["provider"]["contract_type"]
            # Remap BEFORE building the continuous symbol: Databento is asked for
            # the Micro contract itself (MES.v.0, not ES.v.0), and that same
            # string is what gets stored -- matching the rows already in
            # futures_data, which carry the full continuous symbol.
            base_symbol = self.symbol_remapper.remap(symbol)
            if base_symbol != symbol:
                self.logger.info(f"Remapping futures base symbol {symbol} -> {base_symbol}")
            formatted_symbol: str = f"{base_symbol}.{roll_type}.{contract_type}"
            stype_in = db.SType.CONTINUOUS
            stype_out = db.SType.INSTRUMENT_ID
        elif loaded_asset_type == "EQUITY":
            formatted_symbol = symbol
            stype_in = db.SType.RAW_SYMBOL
            stype_out = db.SType.INSTRUMENT_ID
        else:
            raise ValueError(f"Unsupported asset type: {loaded_asset_type}")

        # Databento's `end` is exclusive; shift by a day so the caller's
        # end_date stays inclusive (otherwise the last requested day is dropped).
        end_date_exclusive = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )

        try:
            # Fetch data
            data = await self._get_range(
                symbol,
                dataset=dataset,
                symbols=formatted_symbol,
                schema=db.Schema.from_str(schema),
                start=start_date,
                end=end_date_exclusive,
                stype_in=stype_in,
                stype_out=stype_out,
            )
            # Convert to DataFrame
            df = data.to_df()
            df["symbol"] = formatted_symbol

            # Check if data is empty
            if df.empty:
                self.logger.warning(
                    f"No data found for {symbol} between {start_date} and {end_date}"
                )
                return pd.DataFrame(
                    columns=["time", "open", "high", "low", "close", "volume", "symbol"]
                )

            if "ts_event" in df.index.names:
                df.reset_index(inplace=True)

            self.logger.info(f"Data fetched successfully for {symbol}.")
            return df

        except Exception as e:
            self.logger.error(f"Error fetching data for {symbol}: {e}")
            raise


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return max(1, int(value)) if value else default
