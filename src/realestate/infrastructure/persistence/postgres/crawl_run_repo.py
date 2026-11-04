"""PostgreSQL repository for managing crawl_runs tracking sessions."""

from typing import TYPE_CHECKING, Any, Dict, Optional

from psycopg.rows import dict_row

from realestate.application.ports.crawl_run_repository import CrawlRunRepository
from realestate.domain.services.scheduling import SiteRunState

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
                row = await cur.fetchone()
        assert row is not None  # INSERT ... RETURNING always yields the row
        return row

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

    async def get_schedule_states(self) -> Dict[str, SiteRunState]:
        """last_run_at, latest full-sweep start and in-progress flag for every configured site."""
        query = """
            SELECT
                c.site_id,
                c.last_run_at,
                (SELECT MAX(r.started_at) FROM crawl_runs r
                  WHERE r.site_id = c.site_id AND r.run_mode = 'full_sweep') AS last_full_sweep_at,
                EXISTS (SELECT 1 FROM crawl_runs r
                         WHERE r.site_id = c.site_id AND r.status = 'running') AS running
            FROM crawler_configs c;
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query)
                rows = await cur.fetchall()
        return {
            r["site_id"]: SiteRunState(
                last_run_at=r["last_run_at"],
                last_full_sweep_at=r["last_full_sweep_at"],
                running=bool(r["running"]),
            )
            for r in rows
        }

    async def fail_interrupted_runs(self, max_age_minutes: int = 60) -> int:
        """On scheduler start: fail 'running' sessions of any site left behind by a dead process."""
        query = """
            UPDATE crawl_runs
            SET status = 'failed',
                finished_at = NOW(),
                error_message = 'Interrupted: still running when the scheduler restarted'
            WHERE status = 'running'
              AND started_at < NOW() - make_interval(mins => %s);
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (max_age_minutes,))
                return cur.rowcount
