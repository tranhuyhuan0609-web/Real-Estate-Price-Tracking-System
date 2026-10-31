"""MongoDB setup and index initialization for raw listings."""

import logging
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)


def init_mongo_indexes(mongo_uri: str, db_name: str) -> None:
    """Ensure indexes on raw_listings collection exist.

    Indexes:
        - listing_key (ASCENDING, unique for quick document lookup)
        - crawl_run_ids (ASCENDING for tracking history / sweeps)
        - last_scraped_at (DESCENDING)
    """
    client: MongoClient | None = None
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db = client[db_name]
        collection = db["raw_listings"]

        collection.create_index([("listing_key", ASCENDING)], unique=True, name="idx_raw_listing_key")
        collection.create_index([("crawl_run_ids", ASCENDING)], name="idx_raw_crawl_run_ids")
        collection.create_index([("last_scraped_at", DESCENDING)], name="idx_raw_last_scraped_at")

        logger.info("Successfully created MongoDB indexes for '%s.raw_listings'", db_name)
    except PyMongoError as exc:
        logger.error("Failed to create MongoDB indexes: %s", exc)
        raise
    finally:
        if client is not None:
            client.close()


def init_dlq_mongo_indexes(mongo_uri: str, db_name: str) -> None:
    """Ensure indexes on dlq_events collection exist.

    Indexes:
        - failed_at (DESCENDING)
        - consumer_group (ASCENDING)
    """
    client: MongoClient | None = None
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db = client[db_name]
        collection = db["dlq_events"]

        collection.create_index([("failed_at", DESCENDING)], name="idx_dlq_failed_at")
        collection.create_index([("consumer_group", ASCENDING)], name="idx_dlq_consumer_group")

        logger.info("Successfully created MongoDB indexes for '%s.dlq_events'", db_name)
    except PyMongoError as exc:
        logger.error("Failed to create MongoDB DLQ indexes: %s", exc)
        raise
    finally:
        if client is not None:
            client.close()

