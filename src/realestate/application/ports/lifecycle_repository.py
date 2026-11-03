from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, Optional, Protocol, Tuple


class LifecycleRepository(Protocol):
    """Protocol for managing crawl run progress, lifecycle queries, and views."""

    async def get_run_progress(self, crawl_run_id: str) -> Optional[Tuple[int, int]]:
        ...

    async def get_run_details(self, crawl_run_id: str) -> Optional[Dict[str, Any]]:
        ...

    async def apply_lifecycle_transaction(
        self,
        site_id: str,
        started_at: datetime,
        crawl_run_id: str,
        max_missing_count: int = 3,
    ) -> Dict[str, int]:
        ...

    async def refresh_gold_views(self) -> None:
        ...

