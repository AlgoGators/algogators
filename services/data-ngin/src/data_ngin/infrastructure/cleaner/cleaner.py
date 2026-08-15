from abc import ABC, abstractmethod

import pandas as pd


class Cleaner(ABC):
    """
    Abstract base class for Cleaner modules responsible for standardizing raw data
    and preparing it for database insertion.

    Methods:
        validate_fields: Ensure required fields are present and valid.
        handle_missing_data: Handle missing or corrupt data based on configuration.
        transform_data: Apply transformations to standardize data formats.
        clean: Main method to validate, handle, and transform raw data.
    """

    @abstractmethod
    def validate_fields(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Validates that required fields are present and valid in the data.

        Args:
            data (pd.DataFrame): The raw data to validate. Must include required columns.

        Returns:
            pd.DataFrame: The validated data, with only rows meeting field requirements.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        pass

    @abstractmethod
    def handle_missing_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Handles missing or corrupt data in the provided dataset.

        Args:
            data (pd.DataFrame): The raw data with missing values.

        Returns:
            pd.DataFrame: The data after handling missing values (e.g., dropped rows or imputed values).

        Notes:
            - This method can be customized by subclasses to drop rows, fill missing values, or flag issues.
            (see data/modules/databento_cleaner.py for an example)
        """
        pass

    @abstractmethod
    def transform_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Transforms raw data into a standardized format.

        Args:
            data (pd.DataFrame): The raw data to transform.

        Returns:
            pd.DataFrame: The transformed, standardized data.

        Notes:
            - Transformation could include renaming columns, changing data types, or formatting timestamps.
        """
        pass

    # Gap detection lives in one place: StalenessChecker.detect_date_gaps
    # (data_ngin.domain.services). Cleaner's detect_time_gaps /
    # log_missing_data copies were deleted -- log_missing_data also carried a
    # logging.basicConfig(filename="data_quality.log") side effect that
    # hijacked the process's root logger.

    def clean(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Main method that orchestrates the cleaning process.

        Args:
            data (pd.DataFrame): The raw data to clean.

        Returns:
            pd.DataFrame: The cleaned data ready for database insertion.

        Process:
            1. Validate fields.
            2. Handle missing or corrupt data.
            3. Transform data into the desired format.
        """
        data: pd.DataFrame = self.validate_fields(data)
        data: pd.DataFrame = self.handle_missing_data(data)
        data: pd.DataFrame = self.transform_data(data)
        return data
