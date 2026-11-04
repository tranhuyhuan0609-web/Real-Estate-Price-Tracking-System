"""Bronze upserts must be idempotent under Kafka re-delivery."""

from datetime import datetime, timezone

from realestate.domain.entities import (
    Category,
    ListingDetailsData,
    ParsedListingItem,
    PropertyType,
    RawEventMessage,
)
from realestate.infrastructure.persistence.mongo.bronze_repo import MongoBronzeRepository


def _event(run_id: str) -> RawEventMessage:
    return RawEventMessage(
        listing_key="meeyland_111",
        site_id="meeyland",
        crawl_run_id=run_id,
        scraped_at=datetime.now(timezone.utc),
        parsed_data=ParsedListingItem(
            listing_key="meeyland_111",
            source="meeyland",
            category=Category.SALE,
            property_type=PropertyType.HOUSE,
            details=ListingDetailsData(url="https://example.com/111", title="Test"),
        ),
        raw_payload={"code": 111},
    )


async def test_redelivery_does_not_duplicate(mongo_uri):
    repo = MongoBronzeRepository(mongo_uri=mongo_uri, db_name="it_bronze")
    try:
        await repo.collection.delete_many({})
        await repo.bulk_upsert_raw_events([_event("run_1")])
        await repo.bulk_upsert_raw_events([_event("run_1")])  # re-delivered
        await repo.bulk_upsert_raw_events([_event("run_2")])

        docs = await repo.collection.find({"listing_key": "meeyland_111"}).to_list()
        assert len(docs) == 1
        assert sorted(docs[0]["crawl_run_ids"]) == ["run_1", "run_2"]
    finally:
        await repo.close()


async def test_replay_from_mongo_filters_and_deletes(mongo_uri):
    from unittest.mock import AsyncMock

    from pymongo import AsyncMongoClient

    from realestate.infrastructure.messaging.dlq import replay_from_mongo

    client = AsyncMongoClient(mongo_uri)
    coll = client["it_dlq"]["dlq_events"]
    await coll.delete_many({})
    await coll.insert_many(
        [
            {"_id": "a", "raw_key": "mogi_1", "raw_payload": {"site_id": "mogi"}},
            {"_id": "b", "raw_key": "meeyland_2", "raw_payload": {"site_id": "meeyland"}},
        ]
    )
    publisher = AsyncMock()
    try:
        n = await replay_from_mongo(
            mongo_uri=mongo_uri,
            db_name="it_dlq",
            target_topic="raw",
            publisher=publisher,
            site_id="mogi",
            delete_on_replay=True,
        )
        assert n == 1
        publisher.publish.assert_awaited_once_with(
            topic="raw", key="mogi_1", value={"site_id": "mogi"}
        )
        assert [d["_id"] async for d in coll.find({})] == ["b"]
    finally:
        await client.close()
