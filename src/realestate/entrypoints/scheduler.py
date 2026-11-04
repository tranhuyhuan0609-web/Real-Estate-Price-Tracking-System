"""Crawler scheduler.

Every poll it loads the enabled site configs, asks the domain scheduling policy
(``decide_run``) which sites are due, and starts ``CrawlSiteUseCase`` for each. After a
full sweep it applies the listing lifecycle and refreshes the Gold views.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from realestate.bootstrap import AppContainer
from realestate.domain.entities import RunMode
from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.domain.services.scheduling import SiteRunState, decide_run
from realestate.entrypoints._runtime import configure_runtime, run_service

logger = logging.getLogger("crawler_scheduler")


class CrawlerSchedulerService:
    """Polls crawler_configs and launches due crawls, at most one per site at a time."""

    def __init__(
        self,
        container: AppContainer | None = None,
        *,
        poll_interval_sec: float = 15.0,
        run_once: bool = False,
    ) -> None:
        self.container = container or AppContainer()
        self.poll_interval_sec = poll_interval_sec
        self.run_once = run_once
        self.shutdown_event = asyncio.Event()
        self.active_crawls: set[str] = set()
        # Full-sweep slot already launched per site. Guards against relaunching the same slot
        # when the run failed before it could be recorded in crawl_runs.
        self._launched_slots: dict[str, datetime] = {}

    async def get_due_sites(self, now: datetime | None = None) -> list[tuple[CrawlerConfig, RunMode]]:
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
        try:
            run_id = await self.container.crawl_site_uc.execute(site_id, run_mode=mode)
            logger.info("Completed crawl session for '%s': run_id=%s", site_id, run_id)
            if mode is RunMode.FULL_SWEEP and run_id:
                await self._after_full_sweep(run_id)
        except Exception:
            logger.exception("Error executing crawl for '%s'", site_id)
        finally:
            self.active_crawls.discard(site_id)

    async def _after_full_sweep(self, run_id: str) -> None:
        lifecycle = self.container.apply_lifecycle_uc
        try:
            # Let the Silver/Gold consumers drain this run's backlog first, otherwise listings
            # still waiting in Kafka would be counted as missing.
            if await lifecycle.wait_for_ingestion(run_id):
                stats = await lifecycle.execute(run_id)
                logger.info("Lifecycle applied for '%s': %s", run_id, stats)
            else:
                logger.warning(
                    "Skipping lifecycle for run '%s': consumers have not ingested enough of its items.",
                    run_id,
                )
            await lifecycle.refresh_gold_views()
        except Exception:
            logger.exception("Lifecycle / view refresh failed for run '%s'", run_id)

    async def run(self) -> None:
        settings = self.container.settings
        logger.info(
            "Starting Crawler Scheduler (postgres=%s:%d/%s, kafka=%s, poll=%.1fs)",
            settings.POSTGRES_HOST, settings.POSTGRES_PORT, settings.POSTGRES_DB,
            settings.KAFKA_BOOTSTRAP_SERVERS, self.poll_interval_sec,
        )
        tasks: set[asyncio.Task[None]] = set()
        async with self.container as c:
            await c.open_pg()
            await c.start_publisher()
            try:
                cleaned = await c.crawl_run_repo.fail_interrupted_runs(max_age_minutes=60)
                if cleaned:
                    logger.info("Marked %d interrupted crawl runs as failed.", cleaned)
            except Exception as exc:
                logger.warning("Could not clean up interrupted crawl runs: %s", exc)

            while not self.shutdown_event.is_set():
                try:
                    for config, mode in await self.get_due_sites():
                        task = asyncio.create_task(self._execute_crawl(config.site_id, mode))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                except Exception:
                    logger.exception("Error during scheduler poll")

                if self.run_once:
                    break
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=self.poll_interval_sec)
                except TimeoutError:
                    pass

            if tasks:
                logger.info("Waiting for %d running crawl tasks to finish...", len(tasks))
                await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Crawler Scheduler stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawler Scheduler")
    parser.add_argument("--poll-interval", type=float, default=15.0, help="Poll interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run one schedule inspection cycle and exit")
    args = parser.parse_args()

    configure_runtime()
    service = CrawlerSchedulerService(poll_interval_sec=args.poll_interval, run_once=args.once)
    run_service(service.run(), service.shutdown_event.set)


if __name__ == "__main__":
    main()
