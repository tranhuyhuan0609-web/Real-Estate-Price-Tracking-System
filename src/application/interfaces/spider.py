"""Spider interface for crawler implementations."""

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, Tuple
from src.domain.entities import ParsedListingItem, RunMode


class Spider(ABC):
    """Abstract interface for site spiders."""

    pages_crawled: int

    @abstractmethod
    async def crawl(
        self,
        run_mode: RunMode = RunMode.INCREMENTAL,
    ) -> AsyncGenerator[Tuple[ParsedListingItem, Dict[str, Any]], None]:
        """Asynchronously yield parsed items and raw payloads."""
        pass

