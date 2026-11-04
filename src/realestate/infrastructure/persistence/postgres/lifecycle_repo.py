"""PostgreSQL implementation of LifecycleRepository."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from psycopg.rows import dict_row

from realestate.application.ports.lifecycle_repository import LifecycleRepository

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
                    # 1. A listing is missing when this feed has seen it before and no feed of
                    #    its portal has seen it since the sweep started (ADR 0005). Joining on
                    #    listing_feeds keeps e.g. a mogi_apartment sweep from counting houses
                    #    that only the mogi feed lists.
                    increment_query = """
                        UPDATE listings l
                        SET missing_count = l.missing_count + 1
                        FROM listing_feeds f
                        WHERE f.listing_key = l.listing_key
                          AND f.site_id = %(feed)s
                          AND l.status = 'active'
                          AND l.last_seen_at < %(started)s;
                    """
                    await cur.execute(increment_query, {"feed": site_id, "started": started_at})
                    incremented_count = cur.rowcount

                    # 2. Expire listings of this feed that reached the missing threshold
                    expire_query = """
                        UPDATE listings l
                        SET status = 'expired',
                            ended_at = NOW()
                        FROM listing_feeds f
                        WHERE f.listing_key = l.listing_key
                          AND f.site_id = %(feed)s
                          AND l.status = 'active'
                          AND l.missing_count >= %(max)s;
                    """
                    await cur.execute(expire_query, {"feed": site_id, "max": max_missing_count})
                    expired_count = cur.rowcount

                    # 3. Mark lifecycle_applied_at
                    await cur.execute(
                        "UPDATE crawl_runs SET lifecycle_applied_at = NOW() WHERE crawl_run_id = %s;",
                        (crawl_run_id,),
                    )

                    return {"incremented": incremented_count, "expired": expired_count}

    async def ensure_history_partitions(self, months_ahead: int = 2) -> int:
        """Create monthly listing_price_history partitions up to ``months_ahead`` (migration 003)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT ensure_price_history_partitions(%s)", (months_ahead,))
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def refresh_gold_views(self) -> None:
        """Refresh analytical materialized views in PostgreSQL concurrently without locking."""
        async with self.pool.connection() as conn:
            await conn.set_autocommit(True)
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ward_avg_price_per_m2;"
                    )
                    await cur.execute(
                        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_steep_price_drops_7d;"
                    )
                    logger.info(
                        "Successfully refreshed Gold Layer materialized views concurrently."
                    )
                except Exception as exc:
                    logger.error("Failed to refresh materialized views concurrently: %s", exc)
                    raise
