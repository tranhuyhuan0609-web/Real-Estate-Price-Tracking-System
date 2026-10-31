"""Lifecycle management use case for expiring vanished listings after full sweep runs."""

from __future__ import annotations

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
                        "SELECT crawl_run_id, site_id, run_mode, status, started_at, lifecycle_applied_at FROM crawl_runs WHERE crawl_run_id = %s;",
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

