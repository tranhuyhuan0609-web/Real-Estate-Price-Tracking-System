"""PostgreSQL repository for managing crawl_runs tracking sessions."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional
from psycopg.rows import dict_row

from src.application.interfaces.crawl_run_repository import CrawlRunRepository

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


class PostgresCrawlRunRepository(CrawlRunRepository):
    """Manages crawl run sessions and metrics in PostgreSQL."""

    def __init__(self, pool: "AsyncConnectionPool") -> None:
        self.pool = pool

    async def create_run(
        self,
        crawl_run_id: str,
        site_id: str,
        run_mode: str = "incremental",
    ) -> Dict[str, Any]:
        """Insert a new crawl_runs session with status 'running'."""
        query = """
            INSERT INTO crawl_runs (crawl_run_id, site_id, run_mode, status, started_at)
            VALUES (%s, %s, %s, 'running', NOW())
            RETURNING crawl_run_id, site_id, run_mode, status, started_at;
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, (crawl_run_id, site_id, run_mode))
                return await cur.fetchone()

    async def update_metrics(
        self,
        crawl_run_id: str,
        *,
        items_scraped: int = 0,
        pages_crawled: int = 0,
    ) -> None:
        """Increment progress counters."""
        query = """
            UPDATE crawl_runs 
            SET items_scraped = items_scraped + %s,
                pages_crawled = GREATEST(pages_crawled, %s)
            WHERE crawl_run_id = %s;
        """
        async with self.pool.connection() as conn:
            await conn.execute(query, (items_scraped, pages_crawled, crawl_run_id))

    async def complete_run(
        self,
        crawl_run_id: str,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> None:
        """Mark crawl session as completed or failed with finished timestamp."""
        query = """
            UPDATE crawl_runs 
            SET status = %s,
                finished_at = NOW(),
                error_message = %s
            WHERE crawl_run_id = %s;
        """
        async with self.pool.connection() as conn:
            await conn.execute(query, (status, error_message, crawl_run_id))

    async def abort_stale_runs(
        self,
        site_id: str,
        max_age_minutes: int = 60,
    ) -> int:
        """Mark stale 'running' sessions older than max_age_minutes as 'failed' to prevent blocking."""
        query = """
            UPDATE crawl_runs 
            SET status = 'failed',
                finished_at = NOW(),
                error_message = 'Session aborted due to stale running status (> ' || %s || ' minutes)'
            WHERE site_id = %s
              AND status = 'running'
              AND started_at < NOW() - (%s || ' minutes')::interval;
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (str(max_age_minutes), site_id, str(max_age_minutes)))
                return cur.rowcount

