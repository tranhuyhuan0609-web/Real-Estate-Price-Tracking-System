from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

from realestate.domain.services.scheduling import SiteRunState


class CrawlRunRepository(Protocol):
    """Protocol for managing crawl runs in the database."""

    async def create_run(
        self,
        crawl_run_id: str,
        site_id: str,
        run_mode: str = "incremental",
    ) -> Dict[str, Any]:
        ...

    async def update_metrics(
        self,
        crawl_run_id: str,
        *,
        items_scraped: int = 0,
        pages_crawled: int = 0,
    ) -> None:
        ...

    async def complete_run(
        self,
        crawl_run_id: str,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> None:
        ...

    async def abort_stale_runs(
        self,
        site_id: str,
        max_age_minutes: int = 60,
    ) -> int:
        ...

    async def get_schedule_states(self) -> Dict[str, SiteRunState]:
        ...

    async def fail_interrupted_runs(self, max_age_minutes: int = 60) -> int:
        ...
