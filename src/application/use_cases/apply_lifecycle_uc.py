"""Lifecycle management use case for expiring vanished listings after full sweep runs."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Dict, Optional
from psycopg.rows import dict_row

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class ApplyLifecycleUseCase:
    """Detects missing listings in full_sweep crawl runs and updates their lifecycle status."""

    def __init__(self, pool: "AsyncConnectionPool") -> None:
        self.pool = pool

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
            async with self.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT items_scraped, items_processed FROM crawl_runs WHERE crawl_run_id = %s;",
                        (crawl_run_id,),
                    )
                    row = await cur.fetchone()
            if not row:
                return False
            scraped, processed = int(row[0] or 0), int(row[1] or 0)

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
        """Apply lifecycle rules to listings for a completed full_sweep crawl run.

        Rules:
            1. Find listings with source = site_id, status = 'active' and last_seen_at < run.started_at.
            2. Increment missing_count = missing_count + 1.
            3. If missing_count >= 3, transition status = 'expired' and set ended_at = NOW().
            4. Record lifecycle_applied_at = NOW() on the crawl_runs record.
        """
        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    # 1. Check crawl run
                    await cur.execute(
                        "SELECT crawl_run_id, site_id, run_mode, status, started_at, lifecycle_applied_at, items_scraped FROM crawl_runs WHERE crawl_run_id = %s;",
                        (crawl_run_id,),
                    )
                    run = await cur.fetchone()

                    if not run:
                        raise ValueError(f"Crawl run '{crawl_run_id}' not found.")

                    if run["run_mode"] != "full_sweep":
                        logger.info("Run '%s' is %s (not full_sweep). Lifecycle check skipped.", crawl_run_id, run["run_mode"])
                        return {"incremented": 0, "expired": 0}

                    if run["lifecycle_applied_at"] is not None:
                        logger.warning("Lifecycle rules already applied for run '%s'.", crawl_run_id)
                        return {"incremented": 0, "expired": 0}

                    # Safety gate: only a clean, completed sweep that actually scraped items may expire
                    # listings. A blocked / failed / empty sweep would otherwise mark everything missing.
                    run_status = run.get("status", "completed")
                    if run_status != "completed":
                        logger.warning(
                            "Run '%s' has status '%s' (not completed). Lifecycle skipped.", crawl_run_id, run_status
                        )
                        return {"incremented": 0, "expired": 0}

                    items_scraped = run.get("items_scraped")
                    if items_scraped is not None and items_scraped <= 0:
                        logger.warning("Run '%s' scraped 0 items. Lifecycle skipped.", crawl_run_id)
                        return {"incremented": 0, "expired": 0}

                    site_id = run["site_id"]
                    started_at = run["started_at"]

                    # 2. Increment missing_count for listings not seen in this sweep
                    increment_query = """
                        UPDATE listings
                        SET missing_count = missing_count + 1
                        WHERE source = %s
                          AND status = 'active'
                          AND last_seen_at < %s;
                    """
                    await cur.execute(increment_query, (site_id, started_at))
                    incremented_count = cur.rowcount

                    # 3. Mark listings with missing_count >= 3 as expired
                    expire_query = """
                        UPDATE listings
                        SET status = 'expired',
                            ended_at = NOW()
                        WHERE source = %s
                          AND status = 'active'
                          AND missing_count >= 3;
                    """
                    await cur.execute(expire_query, (site_id,))
                    expired_count = cur.rowcount

                    # 4. Mark lifecycle_applied_at
                    await cur.execute(
                        "UPDATE crawl_runs SET lifecycle_applied_at = NOW() WHERE crawl_run_id = %s;",
                        (crawl_run_id,),
                    )

                    logger.info(
                        "Lifecycle applied for '%s': missing_count incremented for %d listings, %d marked expired.",
                        crawl_run_id, incremented_count, expired_count
                    )
                    return {"incremented": incremented_count, "expired": expired_count}

    async def refresh_gold_views(self) -> None:
        """Refresh analytical materialized views in PostgreSQL concurrently without locking."""
        async with self.pool.connection() as conn:
            await conn.set_autocommit(True)
            async with conn.cursor() as cur:
                try:
                    await cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ward_avg_price_per_m2;")
                    await cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_steep_price_drops_7d;")
                    logger.info("Successfully refreshed Gold Layer materialized views concurrently.")
                except Exception as exc:
                    logger.error("Failed to refresh materialized views concurrently: %s", exc)
                    raise

