import logging
from abc import ABC, abstractmethod
from typing import Any


class Fetcher(ABC):
    """
    Abstract base class for Fetcher modules responsible for retrieving financial data
    for validated symbols from various data providers.

    Attributes:
        config (Dict[str, Any]): Configuration settings loaded from config.yaml.
        logger (logging.Logger): Logger for fetcher-specific logging.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """
        Initializes the Fetcher with a provided configuration dictionary.

        Args:
            config (Dict[str, Any]): Configuration settings.
        """
        self.config: dict[str, Any] = config
        self.logger: logging.Logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.INFO)

    @abstractmethod
    def fetch_data(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """
        Abstract method to fetch historical data for a given symbol over a specified date range.

        Args:
            symbol (str): The symbol for which to fetch data.
            start_date (str): Start date of the data in 'YYYY-MM-DD' format.
            end_date (str): End date of the data in 'YYYY-MM-DD' format.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each representing a row of data.

        Raises:
            NotImplementedError: If the subclass does not implement this method.
        """
        pass

    async def retrieve(
        self,
        symbol: str,
        loaded_asset_type: str,
        start_date: str,
        end_date: str,
        batch_config: dict[str, Any] | None = None,
    ):
        """
        Uniform fetch entry point. Default implementation delegates straight to
        `fetch_data`; subclasses that support batching (e.g. `BatchDownloadDatabentoFetcher`)
        override this to split the range into batches first. Callers don't need to
        know which behavior a given fetcher class implements.

        Args:
            symbol (str): The symbol to fetch data for.
            loaded_asset_type (str): Type of asset to load (e.g., "FUTURE").
            start_date (str): Start date for fetching.
            end_date (str): End date for fetching.
            batch_config (Dict[str, Any]): Batch-downloading settings (unit, max_units); ignored by non-batching fetchers.
        """
        return await self.fetch_data(
            symbol=symbol,
            loaded_asset_type=loaded_asset_type,
            start_date=start_date,
            end_date=end_date,
        )

    # Gap detection lives in one place: StalenessChecker.detect_date_gaps
    # (data_ngin.domain.services). Fetcher's detect_time_gaps /
    # log_missing_data / fetch_and_validate copies were deleted -- they had no
    # production caller and duplicated the same pd.date_range().difference()
    # scan a third time.
