"""Crawl site use case orchestrating spider, crawl session tracking, and Kafka streaming."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from src.application.interfaces.config_repository import ConfigRepository
from src.application.interfaces.crawl_run_repository import CrawlRunRepository
from src.application.interfaces.event_publisher import EventPublisher
from src.application.interfaces.spider import Spider
from src.domain.entities import CrawlRunStatus, RawEventMessage, RunMode
from src.domain.entities.crawler_config import CrawlerConfig

logger = logging.getLogger(__name__)


class CrawlSiteUseCase:
    """Orchestrates crawling a real estate site, recording the crawl run, and streaming events to Kafka."""

    def __init__(
        self,
        config_repo: ConfigRepository,
        crawl_run_repo: CrawlRunRepository,
        event_publisher: EventPublisher,
        kafka_topic: str = "realestate.raw_events",
    ) -> None:
        self.config_repo = config_repo
        self.crawl_run_repo = crawl_run_repo
        self.event_publisher = event_publisher
        self.kafka_topic = kafka_topic

    async def execute(
        self,
        site_id: str,
        run_mode: RunMode = RunMode.INCREMENTAL,
        spider_override: Optional[Spider] = None,
    ) -> str:
        """Execute crawl session for a site.

        Returns:
            str: crawl_run_id of the executed session.
        """
        config: CrawlerConfig = await self.config_repo.get_config(site_id)
        if not config.enabled:
            logger.info("Crawler for site '%s' is disabled. Skipping execution.", site_id)
            return ""

        crawl_run_id = f"{site_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        mode_str = run_mode.value if isinstance(run_mode, RunMode) else str(run_mode)

        # 0. Abort any stale 'running' sessions older than 60 minutes to prevent blocked scheduler
        if hasattr(self.crawl_run_repo, "abort_stale_runs"):
            stale_count = await self.crawl_run_repo.abort_stale_runs(site_id=site_id, max_age_minutes=60)
            if isinstance(stale_count, (int, float)) and stale_count > 0:
                logger.warning("Aborted %d stale running crawl sessions for site '%s'", int(stale_count), site_id)

        # 1. Open session in crawl_runs
        await self.crawl_run_repo.create_run(
            crawl_run_id=crawl_run_id,
            site_id=site_id,
            run_mode=mode_str,
        )
        logger.info("Started crawl run '%s' for site '%s' (mode: %s)", crawl_run_id, site_id, mode_str)

        if spider_override is not None:
            spider = spider_override
        else:
            from src.infrastructure.crawler.spider_factory import create_spider
            spider = create_spider(config)

        items_count = 0
        batch: list[tuple[str, dict]] = []
        BATCH_SIZE = 20

        async def _flush_batch(b: list[tuple[str, dict]]):
            if not b:
                return
            if len(b) == 1:
                await self.event_publisher.publish(topic=self.kafka_topic, key=b[0][0], value=b[0][1])
            elif hasattr(self.event_publisher, "publish_batch"):
                await self.event_publisher.publish_batch(topic=self.kafka_topic, events=b)
            else:
                for k, v in b:
                    await self.event_publisher.publish(topic=self.kafka_topic, key=k, value=v)
            b.clear()

        try:
            # 2. Stream items from spider and publish to Kafka in batches
            async for parsed_item, raw_payload in spider.crawl(run_mode):
                event_message = RawEventMessage(
                    listing_key=parsed_item.listing_key,
                    site_id=site_id,
                    crawl_run_id=crawl_run_id,
                    scraped_at=datetime.now(timezone.utc),
                    parsed_data=parsed_item,
                    raw_payload=raw_payload,
                )

                batch.append((parsed_item.listing_key, event_message.model_dump(mode="json")))
                items_count += 1

                if len(batch) >= BATCH_SIZE:
                    await _flush_batch(batch)

                if items_count % 50 == 0:
                    await self.crawl_run_repo.update_metrics(
                        crawl_run_id=crawl_run_id,
                        items_scraped=50,
                    )

            if batch:
                await _flush_batch(batch)

            # Update remaining metrics and complete run
            remaining = items_count % 50
            pages_crawled = getattr(spider, "pages_crawled", 1 if items_count > 0 else 0)
            await self.crawl_run_repo.update_metrics(
                crawl_run_id=crawl_run_id,
                items_scraped=remaining,
                pages_crawled=pages_crawled,
            )

            # Crawl health gate. A crawl that was blocked, errored out mid-way or hit the page cap
            # must not look like a clean full sweep, otherwise lifecycle would wrongly expire listings.
            is_full_sweep = mode_str == RunMode.FULL_SWEEP.value
            aborted_reason = getattr(spider, "aborted_reason", None)
            truncated = getattr(spider, "exhausted", None) is False and not aborted_reason
            problem: Optional[str] = None
            if aborted_reason:
                problem = f"Crawl aborted: {aborted_reason}"
            elif truncated:
                problem = "Crawl stopped at the page limit before reaching the end of listings."

            if items_count == 0:
                warning_msg = f"Warning: 0 items scraped for site '{site_id}'. Possible layout change or bot block."
                logger.warning(warning_msg)
                # A full sweep that saw nothing is a failure; an empty incremental run stays a warning.
                await self.crawl_run_repo.complete_run(
                    crawl_run_id=crawl_run_id,
                    status=(CrawlRunStatus.FAILED if is_full_sweep else CrawlRunStatus.COMPLETED).value,
                    error_message=warning_msg,
                )
            elif problem:
                logger.warning("Crawl run '%s' for site '%s': %s", crawl_run_id, site_id, problem)
                # Only full sweeps need to be flagged failed (they drive lifecycle expiry);
                # incremental runs keep their already-published items and just carry the warning.
                await self.crawl_run_repo.complete_run(
                    crawl_run_id=crawl_run_id,
                    status=(CrawlRunStatus.FAILED if is_full_sweep else CrawlRunStatus.COMPLETED).value,
                    error_message=problem,
                )
            else:
                await self.crawl_run_repo.complete_run(
                    crawl_run_id=crawl_run_id,
                    status=CrawlRunStatus.COMPLETED.value,
                )
            if hasattr(self.config_repo, "update_last_run"):
                try:
                    await self.config_repo.update_last_run(site_id)
                except Exception as update_err:
                    logger.warning("Could not update last_run_at for site '%s': %s", site_id, update_err)

            logger.info("Successfully completed crawl run '%s' with %d items.", crawl_run_id, items_count)
            return crawl_run_id

        except Exception as exc:
            logger.error("Crawl run '%s' failed: %s", crawl_run_id, exc, exc_info=True)
            await self.crawl_run_repo.complete_run(
                crawl_run_id=crawl_run_id,
                status=CrawlRunStatus.FAILED.value,
                error_message=str(exc),
            )
            raise

