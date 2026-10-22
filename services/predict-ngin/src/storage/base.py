"""
Abstract base class for data stores.

All stores follow a common interface for data persistence.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, TypeVar
import logging

import pandas as pd

T = TypeVar("T")


@dataclass
class StoreStats:
    """Statistics about stored data."""

    record_count: int
    file_count: int
    size_bytes: int
    oldest_record: Optional[datetime]
    newest_record: Optional[datetime]
    partitions: List[str]


class BaseStore(ABC, Generic[T]):
    """
    Abstract base class for data stores.

    Subclasses must implement:
    - append(): Add new records
    - load(): Load records with filtering
    """

    def __init__(
        self,
        base_dir: str,
        name: str = "store",
        logger: Optional[logging.Logger] = None,
    ):
        self.base_dir = Path(base_dir)
        self.name = name
        self.logger = logger or logging.getLogger(f"storage.{name}")

    @abstractmethod
    def append(self, data: List[T], **kwargs) -> int:
        """
        Append new records to the store.

        Args:
            data: List of records to append

        Returns:
            Number of records stored
        """
        pass

    @abstractmethod
    def load(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Load records from the store.

        Args:
            start_date: Filter records after this date
            end_date: Filter records before this date

        Returns:
            DataFrame with loaded records
        """
        pass

    def available(self) -> bool:
        """Check if the store has any data."""
        return self.base_dir.exists() and any(self.base_dir.iterdir())

    def ensure_dir(self) -> Path:
        """Ensure storage directory exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return self.base_dir

    def list_partitions(self) -> List[str]:
        """
        List available partitions (e.g., months).

        Returns:
            List of partition identifiers
        """
        if not self.base_dir.exists():
            return []

        # Default: list all parquet files
        files = sorted(self.base_dir.glob("*.parquet"))
        return [f.stem for f in files]

    def stats(self) -> StoreStats:
        """
        Get statistics about the store.

        Returns:
            StoreStats with summary information
        """
        if not self.base_dir.exists():
            return StoreStats(
                record_count=0,
                file_count=0,
                size_bytes=0,
                oldest_record=None,
                newest_record=None,
                partitions=[],
            )

        files = list(self.base_dir.glob("*.parquet"))
        size_bytes = sum(f.stat().st_size for f in files)
        partitions = self.list_partitions()

        return StoreStats(
            record_count=-1,  # Would need to load to count
            file_count=len(files),
            size_bytes=size_bytes,
            oldest_record=None,  # Would need to load
            newest_record=None,  # Would need to load
            partitions=partitions,
        )

    def delete_partition(self, partition: str) -> bool:
        """
        Delete a specific partition.

        Args:
            partition: Partition identifier

        Returns:
            True if deleted, False if not found
        """
        filepath = self.base_dir / f"{partition}.parquet"
        if filepath.exists():
            filepath.unlink()
            self.logger.info(f"Deleted partition: {partition}")
            return True
        return False

    def compact(self) -> int:
        """
        Compact the store (merge small files, remove duplicates).

        Returns:
            Number of records after compaction
        """
        # Default implementation: no-op
        return -1


__all__ = ["BaseStore", "StoreStats"]
