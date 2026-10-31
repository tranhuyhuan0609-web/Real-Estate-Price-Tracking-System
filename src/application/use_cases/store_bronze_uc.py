"""Use case for storing raw crawler events into Bronze storage layer asynchronously."""

from __future__ import annotations

import logging
from typing import List
from src.application.interfaces.bronze_repository import BronzeRepository
from src.domain.entities import RawEventMessage

logger = logging.getLogger(__name__)


class StoreBronzeUseCase:
    """Consumes RawEventMessages and writes them asynchronously in batches into Bronze layer."""

    def __init__(self, bronze_repo: BronzeRepository) -> None:
        self.bronze_repo = bronze_repo

    async def process_batch(self, events: List[RawEventMessage]) -> int:
        """Process a batch of events into Bronze layer."""
        if not events:
            return 0
        count = await self.bronze_repo.bulk_upsert_raw_events(events)
        logger.info("Bronze layer processed batch of %d events successfully.", len(events))
        return count
