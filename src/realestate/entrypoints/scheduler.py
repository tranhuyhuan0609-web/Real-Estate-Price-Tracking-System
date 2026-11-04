"""Crawler scheduler.

Every poll it loads the enabled site configs, asks the domain scheduling policy
(``decide_run``) which sites are due, and starts ``CrawlSiteUseCase`` for each. After a
full sweep it applies the listing lifecycle and refreshes the Gold views.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime, timezone

from realestate.bootstrap import AppContainer
from realestate.domain.entities import RunMode
from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.domain.services.scheduling import SiteRunState, decide_run
from realestate.entrypoints._runtime import configure_runtime, run_service, serve_metrics
from realestate.infrastructure.observability.metrics import (
    CRAWL_LAST_ITEMS,
    CRAWL_RUNS,
    CRAWL_SECONDS,
    LIFECYCLE_EXPIRED,
    Heartbeat,
    export_backup_ages,
)

logger = logging.getLogger("crawler_scheduler")

PARTITION_CHECK_INTERVAL = 24 * 3600.0
BACKUP_CHECK_INTERVAL = 300.0


class CrawlerSchedulerService:
    """Polls crawler_configs and launches due crawls, at most one per site at a time."""

    def __init__(
        self,
        container: AppContainer | None = None,
        *,
        poll_interval_sec: float = 15.0,
        run_once: bool = False,
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self.container = container or AppContainer()
        self.heartbeat = heartbeat
        self._partitions_checked = 0.0
        self._backups_checked = 0.0
        self._standby_logged = False
        self.poll_interval_sec = poll_interval_sec
        self.run_once = run_once
        self.shutdown_event = asyncio.Event()
        self.active_crawls: set[str] = set()
        # Full-sweep slot already launched per site. Guards against relaunching the same slot
        # when the run failed before it could be recorded in crawl_runs.
        self._launched_slots: dict[str, datetime] = {}

    async def get_due_sites(
        self, now: datetime | None = None
    ) -> list[tuple[CrawlerConfig, RunMode]]:
        now = now or datetime.now(timezone.utc)
        configs = await self.container.config_repo.get_enabled_configs()
        states = await self.container.crawl_run_repo.get_schedule_states()

        due: list[tuple[CrawlerConfig, RunMode]] = []
        for config in configs:
            state = states.get(config.site_id, SiteRunState())
            if config.site_id in self.active_crawls:
                continue
            decision = decide_run(config, state, now)
            if decision is None:
                continue
            if decision.slot is not None:
                if self._launched_slots.get(config.site_id) == decision.slot:
                    continue
                self._launched_slots[config.site_id] = decision.slot
            logger.info(
                "Site '%s' due for %s%s.",
                config.site_id,
                decision.mode.value,
                f" (slot {decision.slot:%Y-%m-%d %H:%M} UTC)" if decision.slot else "",
            )
            due.append((config, decision.mode))
        return due

    async def _execute_crawl(self, site_id: str, mode: RunMode) -> None:
        """Run one crawl; after a full sweep, apply lifecycle and refresh Gold views."""
        self.active_crawls.add(site_id)
        started, result = time.monotonic(), "ok"
        try:
            run_id = await self.container.crawl_site_uc.execute(site_id, run_mode=mode)
            logger.info("Completed crawl session for '%s': run_id=%s", site_id, run_id)
            result = await self._record_run_outcome(site_id, mode, run_id)
            if mode is RunMode.FULL_SWEEP and run_id:
                await self._after_full_sweep(site_id, run_id)
        except Exception:
            result = "error"
            logger.exception("Error executing crawl for '%s'", site_id)
        finally:
            self.active_crawls.discard(site_id)
            CRAWL_RUNS.labels(site_id, mode.value, result).inc()
            CRAWL_SECONDS.labels(site_id, mode.value).observe(time.monotonic() - started)

    async def _record_run_outcome(self, site_id: str, mode: RunMode, run_id: str) -> str:
        """Export what the run achieved; the use case marks bad runs 'failed' without raising."""
        if not run_id:
            return "ok"  # site disabled
        try:
            details = await self.container.lifecycle_repo.get_run_details(run_id)
        except Exception as exc:
            logger.warning("Could not read crawl run '%s': %s", run_id, exc)
            return "ok"
        if not details:
            return "ok"
        CRAWL_LAST_ITEMS.labels(site_id, mode.value).set(details.get("items_scraped") or 0)
        return "ok" if details.get("status") == "completed" else "failed"

    async def _after_full_sweep(self, site_id: str, run_id: str) -> None:
        lifecycle = self.container.apply_lifecycle_uc
        try:
            # Let the Silver/Gold consumers drain this run's backlog first, otherwise listings
            # still waiting in Kafka would be counted as missing.
            if await lifecycle.wait_for_ingestion(run_id):
                stats = await lifecycle.execute(run_id)
                logger.info("Lifecycle applied for '%s': %s", run_id, stats)
                if isinstance(stats, dict):
                    LIFECYCLE_EXPIRED.labels(site_id).inc(int(stats.get("expired") or 0))
            else:
                logger.warning(
                    "Skipping lifecycle for run '%s': consumers have not ingested enough of its items.",
                    run_id,
                )
            await lifecycle.refresh_gold_views()
        except Exception:
            logger.exception("Lifecycle / view refresh failed for run '%s'", run_id)

    async def _sleep(self) -> None:
        try:
            await asyncio.wait_for(self.shutdown_event.wait(), timeout=self.poll_interval_sec)
        except TimeoutError:
            pass

    async def _lead(self) -> bool:
        """True while this scheduler holds the leader lock. Only the leader schedules crawls."""
        lock = self.container.leader_lock
        was_leader = lock.is_leader
        if not await lock.ensure():
            if was_leader or not self._standby_logged:
                logger.info("Another scheduler is the leader; standing by.")
                self._standby_logged = True
            return False
        if not was_leader:
            logger.info("Acquired scheduler leader lock.")
            self._standby_logged = False
            # Runs left 'running' by a previous leader that died.
            try:
                cleaned = await self.container.crawl_run_repo.fail_interrupted_runs(
                    max_age_minutes=60
                )
                if cleaned:
                    logger.info("Marked %d interrupted crawl runs as failed.", cleaned)
            except Exception as exc:
                logger.warning("Could not clean up interrupted crawl runs: %s", exc)
        return True

    def _export_backup_ages(self) -> None:
        backup_dir = self.container.settings.BACKUP_DIR
        now = time.monotonic()
        if not backup_dir or (
            self._backups_checked and now - self._backups_checked < BACKUP_CHECK_INTERVAL
        ):
            return
        self._backups_checked = now
        try:
            export_backup_ages(backup_dir)
        except OSError as exc:
            logger.warning("Could not read backup directory %s: %s", backup_dir, exc)

    async def _maintain_partitions(self) -> None:
        """Keep monthly price-history partitions created ahead (see migration 003)."""
        now = time.monotonic()
        if self._partitions_checked and now - self._partitions_checked < PARTITION_CHECK_INTERVAL:
            return
        self._partitions_checked = now
        try:
            created = await self.container.lifecycle_repo.ensure_history_partitions(months_ahead=2)
            if created:
                logger.info("Created %d price-history partition(s).", created)
        except Exception as exc:
            logger.warning("Could not ensure price-history partitions: %s", exc)

    async def run(self) -> None:
        settings = self.container.settings
        logger.info(
            "Starting Crawler Scheduler (postgres=%s:%d/%s, kafka=%s, poll=%.1fs)",
            settings.POSTGRES_HOST,
            settings.POSTGRES_PORT,
            settings.POSTGRES_DB,
            settings.KAFKA_BOOTSTRAP_SERVERS,
            self.poll_interval_sec,
        )
        tasks: set[asyncio.Task[None]] = set()
        async with self.container as c:
            await c.open_pg()
            await c.start_publisher()

            while not self.shutdown_event.is_set():
                if self.heartbeat is not None:
                    self.heartbeat.beat()  # a standby is healthy too
                if not await self._lead():
                    if self.run_once:
                        break
                    await self._sleep()
                    continue
                await self._maintain_partitions()
                self._export_backup_ages()
                try:
                    for config, mode in await self.get_due_sites():
                        task = asyncio.create_task(self._execute_crawl(config.site_id, mode))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                except Exception:
                    logger.exception("Error during scheduler poll")

                if self.run_once:
                    break
                await self._sleep()

            if tasks:
                logger.info("Waiting for %d running crawl tasks to finish...", len(tasks))
                await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Crawler Scheduler stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawler Scheduler")
    parser.add_argument(
        "--poll-interval", type=float, default=15.0, help="Poll interval in seconds"
    )
    parser.add_argument(
        "--once", action="store_true", help="Run one schedule inspection cycle and exit"
    )
    args = parser.parse_args()

    configure_runtime()
    settings = serve_metrics()
    service = CrawlerSchedulerService(
        poll_interval_sec=args.poll_interval,
        run_once=args.once,
        heartbeat=Heartbeat("scheduler", settings.HEARTBEAT_DIR, min_interval=0),
    )
    run_service(service.run(), service.shutdown_event.set)


if __name__ == "__main__":
    main()
