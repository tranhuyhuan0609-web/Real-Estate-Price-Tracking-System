"""Silver and Gold use case processing raw Kafka messages into PostgreSQL relational layers."""

from __future__ import annotations

import logging
from typing import List, Tuple
from src.application.interfaces.listing_repository import ListingRepository
from src.domain.entities import RawEventMessage

logger = logging.getLogger(__name__)


class ProcessSilverGoldUseCase:
    """Processes raw listing events into Silver (Listings, Advertisers) and Gold (Price History)."""

    def __init__(self, pg_repo: ListingRepository) -> None:
        self.pg_repo = pg_repo

    async def execute(self, event: RawEventMessage, update_crawl_run: bool = True) -> Tuple[bool, bool]:
        """Process a single event message into Silver/Gold storage."""
        is_new, price_changed = await self.pg_repo.ingest_listing_transactional(
            item=event.parsed_data,
            crawl_run_id=event.crawl_run_id,
            update_crawl_run=update_crawl_run,
        )
        logger.debug(
            "Processed Silver/Gold for '%s' (is_new=%s, price_changed=%s)",
            event.listing_key, is_new, price_changed
        )
        return is_new, price_changed

    async def execute_batch(
        self, events: List[RawEventMessage]
    ) -> List[Tuple[RawEventMessage, bool, bool]]:
        """Process a batch of event messages into Silver/Gold storage in a single transaction."""
        if not events:
            return []
        batch = [(e.parsed_data, e.crawl_run_id) for e in events]
        results = await self.pg_repo.ingest_batch_transactional(batch)
        return [(events[i], results[i][0], results[i][1]) for i in range(len(events))]

