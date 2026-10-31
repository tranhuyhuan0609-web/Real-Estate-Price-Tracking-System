"""Lifecycle management use case for expiring vanished listings after full sweep runs."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Dict, Optional, Union
from src.application.interfaces.lifecycle_repository import LifecycleRepository

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class ApplyLifecycleUseCase:
    """Detects missing listings in full_sweep crawl runs and updates their lifecycle status."""

    def __init__(
        self,
        repo_or_pool: Union[LifecycleRepository, "AsyncConnectionPool", None] = None,
        *,
        pool: Optional["AsyncConnectionPool"] = None,
    ) -> None:
        target = repo_or_pool if repo_or_pool is not None else pool
        if target is None:
            raise ValueError("Either 'repo' or 'pool' must be provided to ApplyLifecycleUseCase.")
        if isinstance(target, LifecycleRepository):
            self.repo = target
        else:
            from src.infrastructure.database.pg_lifecycle_repo import PgLifecycleRepository
            self.repo = PgLifecycleRepository(target)

    async def wait_for_ingestion(
        self,
        crawl_run_id: str,
        *,
        timeout_sec: float = 600.0,
        idle_sec: float = 60.0,
        poll_sec: float = 5.0,
        min_ratio: float = 0.95,
    ) -> bool:
        """Wait until the Silver/Gold consumers have processed (almost) all items of a crawl run.

        Lifecycle compares listings.last_seen_at with the run start, so it must not run while
        consumers still have a backlog for this run. Returns True when it is safe to continue:
        every scraped item was processed, or progress stalled with at least ``min_ratio`` done
        (a few items may have gone to the DLQ and are never counted as processed).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_sec
        last_progress_at = loop.time()
        last_processed = -1
        scraped = processed = 0

        while True:
            progress = await self.repo.get_run_progress(crawl_run_id)
            if not progress:
                return False
            scraped, processed = progress

            if scraped > 0 and processed >= scraped:
                return True
            now = loop.time()
            if processed != last_processed:
                last_processed = processed
                last_progress_at = now
            if now >= deadline or now - last_progress_at >= idle_sec:
                break
            await asyncio.sleep(poll_sec)

        ok = scraped > 0 and processed >= scraped * min_ratio
        logger.warning(
            "Ingestion wait for run '%s' ended: processed %d / scraped %d (%s).",
            crawl_run_id, processed, scraped, "accepted" if ok else "too low, lifecycle should be skipped",
        )
        return ok

    async def execute(self, crawl_run_id: str) -> Dict[str, int]:
        """Apply lifecycle rules to listings for a completed full_sweep crawl run."""
        run = await self.repo.get_run_details(crawl_run_id)
        if not run:
            raise ValueError(f"Crawl run '{crawl_run_id}' not found.")

        if run["run_mode"] != "full_sweep":
            logger.info("Run '%s' is %s (not full_sweep). Lifecycle check skipped.", crawl_run_id, run["run_mode"])
            return {"incremented": 0, "expired": 0}

        if run["lifecycle_applied_at"] is not None:
            logger.warning("Lifecycle rules already applied for run '%s'.", crawl_run_id)
            return {"incremented": 0, "expired": 0}

        # Safety gate: only a clean, completed sweep that actually scraped items may expire listings.
        run_status = run.get("status", "completed")
        if run_status != "completed":
            logger.warning("Run '%s' has status '%s' (not completed). Lifecycle skipped.", crawl_run_id, run_status)
            return {"incremented": 0, "expired": 0}

        items_scraped = run.get("items_scraped")
        if items_scraped is not None and items_scraped <= 0:
            logger.warning("Run '%s' scraped 0 items. Lifecycle skipped.", crawl_run_id)
            return {"incremented": 0, "expired": 0}

        site_id = run["site_id"]
        started_at = run["started_at"]

        stats = await self.repo.apply_lifecycle_transaction(
            site_id=site_id,
            started_at=started_at,
            crawl_run_id=crawl_run_id,
            max_missing_count=3,
        )

        logger.info(
            "Lifecycle applied for '%s': missing_count incremented for %d listings, %d marked expired.",
            crawl_run_id, stats["incremented"], stats["expired"]
        )
        return stats

    async def refresh_gold_views(self) -> None:
        """Refresh analytical materialized views in PostgreSQL concurrently without locking."""
        await self.repo.refresh_gold_views()
