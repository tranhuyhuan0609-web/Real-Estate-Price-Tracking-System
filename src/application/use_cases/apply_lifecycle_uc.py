"""Use case for executing the listing lifecycle after a successful crawl run."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from src.application.interfaces.lifecycle_repository import LifecycleRepository

logger = logging.getLogger(__name__)

DEFAULT_MAX_MISSING_COUNT: int = 3


class ApplyLifecycleUseCase:
    """Executes the lifecycle state transitions for listings after a crawl run completes."""

    def __init__(
        self,
        repo: LifecycleRepository,
        *,
        default_max_missing_count: int = DEFAULT_MAX_MISSING_COUNT,
    ) -> None:
        if repo is None:
            raise ValueError("A LifecycleRepository instance must be provided to ApplyLifecycleUseCase.")
        self.repo: LifecycleRepository = repo
        self.default_max_missing_count = default_max_missing_count

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
        scraped items are still buffered in Kafka or midway through batch ingestion.
        """
        deadline = asyncio.get_running_loop().time() + timeout_sec
        last_progress_at = asyncio.get_running_loop().time()
        last_processed = -1

        while asyncio.get_running_loop().time() < deadline:
            progress = await self.repo.get_run_progress(crawl_run_id)
            if not progress:
                logger.warning("Run '%s' not found while waiting for ingestion.", crawl_run_id)
                return False

            scraped, processed = progress

            if scraped == 0:
                return True

            if processed >= scraped or (scraped > 0 and (processed / scraped) >= min_ratio):
                logger.info(
                    "Ingestion gate reached for '%s': %d/%d processed (ratio: %.2f)",
                    crawl_run_id, processed, scraped, (processed / scraped) if scraped else 1.0,
                )
                return True

            now = asyncio.get_running_loop().time()
            if processed > last_processed:
                last_processed = processed
                last_progress_at = now
            elif now - last_progress_at >= idle_sec:
                if scraped > 0 and (processed / scraped) >= min_ratio:
                    return True
                logger.warning(
                    "Ingestion idle for %.1fs on run '%s' at %d/%d items (below min_ratio %.2f). Ingestion stalled.",
                    now - last_progress_at, crawl_run_id, processed, scraped, min_ratio,
                )
                return False

            await asyncio.sleep(poll_sec)

        logger.error("Timed out waiting for ingestion on run '%s' after %.1fs", crawl_run_id, timeout_sec)
        return False

    async def execute(
        self,
        crawl_run_id: str,
        *,
        max_missing_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Apply lifecycle transitions for a completed crawl run."""
        run = await self.repo.get_run_details(crawl_run_id)
        if not run:
            logger.warning("Crawl run '%s' not found. Lifecycle skipped.", crawl_run_id)
            return {"incremented": 0, "expired": 0}

        run_status = run.get("status")
        if run_status != "completed":
            logger.warning("Run '%s' has status '%s' (not completed). Lifecycle skipped.", crawl_run_id, run_status)
            return {"incremented": 0, "expired": 0}

        run_mode = run.get("run_mode")
        if run_mode != "full_sweep":
            logger.info("Run '%s' has mode '%s' (not full_sweep). Lifecycle skipped.", crawl_run_id, run_mode)
            return {"incremented": 0, "expired": 0}

        items_scraped = run.get("items_scraped")
        if items_scraped is not None and items_scraped <= 0:
            logger.warning("Run '%s' scraped 0 items. Lifecycle skipped.", crawl_run_id)
            return {"incremented": 0, "expired": 0}

        site_id = run["site_id"]
        started_at = run["started_at"]
        limit = max_missing_count if max_missing_count is not None else self.default_max_missing_count

        stats = await self.repo.apply_lifecycle_transaction(
            site_id=site_id,
            started_at=started_at,
            crawl_run_id=crawl_run_id,
            max_missing_count=limit,
        )

        logger.info(
            "Lifecycle applied for '%s': missing_count incremented for %d listings, %d marked expired.",
            crawl_run_id, stats["incremented"], stats["expired"]
        )
        return stats

    async def refresh_gold_views(self) -> None:
        """Refresh analytical materialized views in PostgreSQL concurrently without locking."""
        await self.repo.refresh_gold_views()
