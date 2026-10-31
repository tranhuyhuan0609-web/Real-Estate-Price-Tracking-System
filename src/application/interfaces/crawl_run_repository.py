"""Crawl run repository interface for managing crawl sessions."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class CrawlRunRepository(ABC):
    """Abstract interface for managing crawl runs in the database."""

    @abstractmethod
    async def create_run(
        self,
        crawl_run_id: str,
        site_id: str,
        run_mode: str = "incremental",
    ) -> Dict[str, Any]:
        """Create a new crawl session."""
        pass

    @abstractmethod
    async def update_metrics(
        self,
        crawl_run_id: str,
        *,
        items_scraped: int = 0,
        pages_crawled: int = 0,
    ) -> None:
        """Update metrics for an active run."""
        pass

    @abstractmethod
    async def complete_run(
        self,
        crawl_run_id: str,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> None:
        """Complete a crawl session."""
        pass

    @abstractmethod
    async def abort_stale_runs(
        self,
        site_id: str,
        max_age_minutes: int = 60,
    ) -> int:
        """Abort stale running sessions."""
        pass

