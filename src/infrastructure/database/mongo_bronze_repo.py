"""Asynchronous MongoDB repository for Bronze layer raw listings storage."""

from __future__ import annotations

import logging
from typing import List
from pymongo import AsyncMongoClient, UpdateOne
from pymongo.errors import PyMongoError
from src.domain.entities import RawEventMessage

logger = logging.getLogger(__name__)


class MongoBronzeRepository:
    """Asynchronously stores raw ingestion payloads and crawl run histories in MongoDB."""

    def __init__(self, mongo_uri: str, db_name: str, collection_name: str = "raw_listings") -> None:
        self.client: AsyncMongoClient = AsyncMongoClient(mongo_uri)
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]

    async def bulk_upsert_raw_events(self, events: List[RawEventMessage]) -> int:
        """Upsert a batch of raw events into MongoDB raw_listings collection asynchronously.

        Updates the latest JSON payload while keeping an array of all crawl_run_ids that saw the listing.
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
            logger.debug("Bronze bulk_write succeeded: upserted=%d, modified=%d", result.upserted_count, result.modified_count)
            return upserted_count
        except PyMongoError as exc:
            logger.error("Failed to execute bulk_write to MongoDB Bronze collection: %s", exc)
            raise

    async def close(self) -> None:
        """Close asynchronous MongoDB client connection."""
        await self.client.close()
