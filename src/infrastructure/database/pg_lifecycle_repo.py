"""PostgreSQL implementation of LifecycleRepository."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple
from psycopg.rows import dict_row
from src.application.interfaces.lifecycle_repository import LifecycleRepository

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class PgLifecycleRepository(LifecycleRepository):
    """PostgreSQL repository handling crawl run lifecycle progression and materialized view refreshes."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def get_run_progress(self, crawl_run_id: str) -> Optional[Tuple[int, int]]:
        """Return (items_scraped, items_processed) for a crawl run."""
        query = "SELECT items_scraped, items_processed FROM crawl_runs WHERE crawl_run_id = %s;"
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (crawl_run_id,))
                row = await cur.fetchone()
                if not row:
                    return None
                return int(row[0] or 0), int(row[1] or 0)

    async def get_run_details(self, crawl_run_id: str) -> Optional[Dict[str, Any]]:
        """Return details for a crawl run."""
        query = """
            SELECT crawl_run_id, site_id, run_mode, status, started_at, lifecycle_applied_at, items_scraped
            FROM crawl_runs
            WHERE crawl_run_id = %s;
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, (crawl_run_id,))
                return await cur.fetchone()

    async def apply_lifecycle_transaction(
        self,
        site_id: str,
        started_at: datetime,
        crawl_run_id: str,
        max_missing_count: int = 3,
    ) -> Dict[str, int]:
        """Apply lifecycle updates (increment missing count, expire listings) in a single transaction."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    # 1. Increment missing_count for listings not seen in this sweep
                    increment_query = """
                        UPDATE listings
                        SET missing_count = missing_count + 1
                        WHERE source = %s
                          AND status = 'active'
                          AND last_seen_at < %s;
                    """
                    await cur.execute(increment_query, (site_id, started_at))
                    incremented_count = cur.rowcount

                    # 2. Mark listings with missing_count >= max_missing_count as expired
                    expire_query = """
                        UPDATE listings
                        SET status = 'expired',
                            ended_at = NOW()
                        WHERE source = %s
                          AND status = 'active'
                          AND missing_count >= %s;
                    """
                    await cur.execute(expire_query, (site_id, max_missing_count))
                    expired_count = cur.rowcount

                    # 3. Mark lifecycle_applied_at
                    await cur.execute(
                        "UPDATE crawl_runs SET lifecycle_applied_at = NOW() WHERE crawl_run_id = %s;",
                        (crawl_run_id,),
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
