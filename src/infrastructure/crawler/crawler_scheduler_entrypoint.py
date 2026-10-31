"""Scheduler service: Crawler Scheduler Entrypoint.

Periodically inspects crawler_configs in PostgreSQL, identifies sites due for incremental
or full_sweep crawls, and triggers CrawlSiteUseCase sessions streaming to Kafka.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set
import psycopg_pool
from psycopg.rows import dict_row

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from src.application.use_cases.apply_lifecycle_uc import ApplyLifecycleUseCase
from src.application.use_cases.crawl_site_uc import CrawlSiteUseCase
from src.domain.entities import RunMode
from src.infrastructure.config.main import load_settings
from src.infrastructure.database.postgres_config_repository import PostgresConfigRepository
from src.infrastructure.database.postgres_crawl_run_repository import PostgresCrawlRunRepository
from src.infrastructure.kafka.producer import AioKafkaEventPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("crawler_scheduler")


class CrawlerSchedulerService:
    """Orchestrates automated crawler runs based on DB schedules and intervals."""

    def __init__(
        self,
        *,
        poll_interval_sec: float = 15.0,
        run_once: bool = False,
    ) -> None:
        self.settings = load_settings()
        self.poll_interval_sec = poll_interval_sec
        self.run_once = run_once
        self.shutdown_event = asyncio.Event()
        self.active_crawls: Set[str] = set()

    async def _get_active_running_sites(self, pool: psycopg_pool.AsyncConnectionPool) -> Set[str]:
        """Fetch all sites that currently have an active 'running' crawl session."""
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT DISTINCT site_id FROM crawl_runs WHERE status = 'running'")
                rows = await cur.fetchall()
                return {r[0] for r in rows}

    async def _get_due_sites(self, pool: psycopg_pool.AsyncConnectionPool) -> List[Dict]:
        """Query enabled crawler configurations and determine which sites are due for crawling."""
        query = """
            SELECT 
                site_id,
                name,
                enabled,
                interval_seconds,
                schedule_times,
                last_run_at
            FROM crawler_configs
            WHERE enabled = TRUE
            ORDER BY site_id
        """
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query)
                configs = await cur.fetchall()

        running_in_db = await self._get_active_running_sites(pool)
        now_utc = datetime.now(timezone.utc)
        now_hm = now_utc.strftime("%H:%M")

        due_tasks = []
        for cfg in configs:
            site_id = cfg["site_id"]
            if site_id in self.active_crawls or site_id in running_in_db:
                logger.debug("Site '%s' is already currently crawling. Skipping.", site_id)
                continue

            interval_sec = cfg.get("interval_seconds") or 300
            last_run_at = cfg.get("last_run_at")
            schedule_times = cfg.get("schedule_times") or []

            # Check full sweep schedule match
            is_full_sweep = False
            if schedule_times and any(s == now_hm for s in schedule_times):
                is_full_sweep = True

            # Check interval due
            is_due = False
            if last_run_at is None:
                is_due = True
                logger.info("Site '%s' has never run yet. Triggering initial crawl.", site_id)
            else:
                elapsed = (now_utc - last_run_at).total_seconds()
                if elapsed >= interval_sec or is_full_sweep:
                    is_due = True
                    logger.info("Site '%s' is due (elapsed: %.1fs >= interval: %ds).", site_id, elapsed, interval_sec)

            if is_due:
                mode = RunMode.FULL_SWEEP if is_full_sweep else RunMode.INCREMENTAL
                due_tasks.append({"site_id": site_id, "mode": mode, "name": cfg.get("name")})

        return due_tasks

    async def _execute_crawl(
        self,
        use_case: CrawlSiteUseCase,
        site_id: str,
        mode: RunMode,
        pool: Optional[psycopg_pool.AsyncConnectionPool] = None,
    ) -> None:
        """Run crawl for a specific site and release the concurrency lock upon completion."""
        self.active_crawls.add(site_id)
        try:
            logger.info("Launching crawl session for '%s' in mode '%s'...", site_id, mode.value)
            run_id = await use_case.execute(site_id, run_mode=mode)
            logger.info("Completed crawl session for '%s': run_id=%s", site_id, run_id)

            if pool is not None and run_id:
                try:
                    lifecycle_uc = ApplyLifecycleUseCase(pool)
                    if mode == RunMode.FULL_SWEEP:
                        logger.info("Triggering automated lifecycle & view refresh for FULL_SWEEP run '%s'...", run_id)
                        stats = await lifecycle_uc.execute(run_id)
                        logger.info("Automated lifecycle completed for '%s': %s", run_id, stats)
                    await lifecycle_uc.refresh_gold_views()
                    logger.info("Materialized views refreshed successfully for run '%s'", run_id)
                except Exception as life_err:
                    logger.error("Failed to run automated lifecycle / view refresh for '%s': %s", run_id, life_err)
        except Exception as exc:
            logger.error("Error executing crawl for '%s': %s", site_id, exc, exc_info=True)
        finally:
            self.active_crawls.discard(site_id)

    async def run(self) -> None:
        logger.info("Starting Crawler Scheduler...")
        logger.info("PostgreSQL: %s:%d | DB: %s", self.settings.POSTGRES_HOST, self.settings.POSTGRES_PORT, self.settings.POSTGRES_DB)
        logger.info("Kafka: %s | Raw Topic: %s", self.settings.KAFKA_BOOTSTRAP_SERVERS, self.settings.KAFKA_RAW_TOPIC)
        logger.info("Scheduler poll interval: %.1f seconds", self.poll_interval_sec)

        event_publisher = AioKafkaEventPublisher(self.settings.KAFKA_BOOTSTRAP_SERVERS)
        await event_publisher.start()

        async with psycopg_pool.AsyncConnectionPool(conninfo=self.settings.postgres_dsn) as pool:
            # Clean up stale running crawl runs left over from prior process termination
            try:
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "UPDATE crawl_runs SET status = 'failed', error_message = 'Interrupted by scheduler restart' WHERE status = 'running'"
                        )
                        if cur.rowcount > 0:
                            logger.info("Cleaned up %d stale running crawl runs on startup.", cur.rowcount)
            except Exception as clean_err:
                logger.warning("Could not clean up stale running crawl runs: %s", clean_err)

            config_repo = PostgresConfigRepository(pool)
            crawl_run_repo = PostgresCrawlRunRepository(pool)
            use_case = CrawlSiteUseCase(
                config_repo=config_repo,
                crawl_run_repo=crawl_run_repo,
                event_publisher=event_publisher,
                kafka_topic=self.settings.KAFKA_RAW_TOPIC,
            )

            background_tasks: Set[asyncio.Task] = set()

            try:
                while not self.shutdown_event.is_set():
                    try:
                        due_sites = await self._get_due_sites(pool)
                        for item in due_sites:
                            site_id = item["site_id"]
                            mode = item["mode"]
                            task = asyncio.create_task(self._execute_crawl(use_case, site_id, mode, pool=pool))
                            background_tasks.add(task)
                            task.add_done_callback(background_tasks.discard)

                        if self.run_once:
                            logger.info("Scheduler --once mode enabled. Waiting for %d active crawls...", len(background_tasks))
                            if background_tasks:
                                await asyncio.gather(*background_tasks, return_exceptions=True)
                            logger.info("All --once crawl tasks completed. Exiting.")
                            break

                    except Exception as loop_err:
                        logger.error("Error during scheduler loop check: %s", loop_err, exc_info=True)

                    try:
                        await asyncio.wait_for(self.shutdown_event.wait(), timeout=self.poll_interval_sec)
                    except asyncio.TimeoutError:
                        pass

                if background_tasks:
                    logger.info("Waiting for %d running crawl tasks to finish before exit...", len(background_tasks))
                    await asyncio.gather(*background_tasks, return_exceptions=True)

            finally:
                logger.info("Shutting down Crawler Scheduler...")
                await event_publisher.stop()
                logger.info("Crawler Scheduler stopped cleanly.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawler Scheduler Entrypoint")
    parser.add_argument("--poll-interval", type=float, default=15.0, help="Poll interval in seconds (default: 15.0)")
    parser.add_argument("--once", action="store_true", help="Run one schedule inspection cycle and exit")
    args = parser.parse_args()

    service = CrawlerSchedulerService(
        poll_interval_sec=args.poll_interval,
        run_once=args.once,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal():
        logger.info("Shutdown signal received in scheduler, exiting gracefully...")
        service.shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            signal.signal(sig, lambda *_: handle_signal())

    try:
        loop.run_until_complete(service.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

