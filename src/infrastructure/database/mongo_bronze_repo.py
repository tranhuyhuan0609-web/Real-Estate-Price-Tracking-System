"""Bronze layer repository implementation for MongoDB."""

from __future__ import annotations

import logging
from typing import List, Optional
from pymongo import AsyncMongoClient, UpdateOne
from src.application.interfaces.bronze_repository import BronzeRepository
from src.domain.entities import RawEventMessage

logger = logging.getLogger(__name__)


class MongoBronzeRepository(BronzeRepository):
    """Bronze repository that persists raw scraped items asynchronously into MongoDB."""

    def __init__(self, uri: str, db_name: str, collection_name: str = "raw_listings") -> None:
        self.client: AsyncMongoClient = AsyncMongoClient(uri)
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]

    async def bulk_upsert_raw_events(self, events: List[RawEventMessage]) -> int:
        """Upsert a batch of raw events into MongoDB raw_listings collection asynchronously.

        Updates the latest JSON payload while keeping a set of unique crawl_run_ids that saw the listing
        to ensure idempotency when Kafka re-delivers messages.
        """
        if not events:
            return 0

        operations = []
        for event in events:
            update_doc = {
                "$set": {
                    "site_id": event.site_id,
                    "latest_raw_payload": event.raw_payload,
                    "last_scraped_at": event.scraped_at,
                },
                "$addToSet": {
                    "crawl_run_ids": event.crawl_run_id,
                },
                "$setOnInsert": {
                    "listing_key": event.listing_key,
                    "first_scraped_at": event.scraped_at,
                },
            }

            operations.append(
                UpdateOne(
                    filter={"listing_key": event.listing_key},
                    update=update_doc,
                    upsert=True,
                )
            )

        try:
            result = await self.collection.bulk_write(operations, ordered=False)
            upserted_count = (result.upserted_count or 0) + (result.modified_count or 0)
            return upserted_count
        except Exception as exc:
            logger.error("Failed to execute bulk_write on bronze MongoDB: %s", exc, exc_info=True)
            raise

    async def close(self) -> None:
        """Close MongoDB connection gracefully."""
        await self.client.close()
