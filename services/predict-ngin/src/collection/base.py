"""
Abstract base class for data collectors.

All collectors follow a common interface for fetching and storing data.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TypeVar, Generic
import logging

T = TypeVar("T")


@dataclass
class CollectionResult:
    """Result of a data collection operation."""

    success: bool
    records_fetched: int
    records_stored: int
    start_time: datetime
    end_time: datetime
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()


class BaseCollector(ABC, Generic[T]):
    """
    Abstract base class for data collectors.

    Subclasses must implement:
    - fetch(): Fetch data from source
    - store(): Store fetched data
    """

    def __init__(
        self,
        name: str = "collector",
        output_dir: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.name = name
        self.output_dir = Path(output_dir) if output_dir else Path("data")
        self.logger = logger or logging.getLogger(f"collection.{name}")

    @abstractmethod
    async def fetch(self, **kwargs) -> List[T]:
        """
        Fetch data from the source.

        Returns:
            List of fetched data items
        """
        pass

    @abstractmethod
    def store(self, data: List[T], **kwargs) -> int:
        """
        Store fetched data.

        Args:
            data: List of data items to store

        Returns:
            Number of records stored
        """
        pass

    async def collect(self, **kwargs) -> CollectionResult:
        """
        Fetch and store data in one operation.

        Returns:
            CollectionResult with operation details
        """
        start_time = datetime.utcnow()
        errors: List[str] = []
        records_fetched = 0
        records_stored = 0

        try:
            data = await self.fetch(**kwargs)
            records_fetched = len(data)

            if data:
                records_stored = self.store(data, **kwargs)

        except Exception as e:
            self.logger.error(f"Collection failed: {e}")
            errors.append(str(e))

        end_time = datetime.utcnow()

        return CollectionResult(
            success=len(errors) == 0,
            records_fetched=records_fetched,
            records_stored=records_stored,
            start_time=start_time,
            end_time=end_time,
            errors=errors,
        )

    def ensure_output_dir(self) -> Path:
        """Ensure output directory exists and return it."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir
