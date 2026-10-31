"""Lifecycle repository interface for managing listing lifecycles and view refreshes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


class LifecycleRepository(ABC):
    """Abstract interface for managing crawl run progress, lifecycle queries, and views."""

    @abstractmethod
    async def get_run_progress(self, crawl_run_id: str) -> Optional[Tuple[int, int]]:
        """Return (items_scraped, items_processed) for a crawl run."""
        pass

    @abstractmethod
    async def get_run_details(self, crawl_run_id: str) -> Optional[Dict[str, Any]]:
        """Return details (site_id, started_at, run_mode, status, items_scraped) for a crawl run."""
        pass

    @abstractmethod
    async def apply_lifecycle_transaction(
        self,
        site_id: str,
        started_at: datetime,
        crawl_run_id: str,
        max_missing_count: int = 3,
    ) -> Dict[str, int]:
        """Apply lifecycle updates (increment missing count, expire listings) in a single transaction."""
        pass

    @abstractmethod
    async def refresh_gold_views(self) -> None:
        """Refresh analytical materialized views concurrently."""
        pass
