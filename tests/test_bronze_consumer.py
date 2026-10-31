"""Unit tests for Bronze Consumer layer."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from src.application.use_cases.store_bronze_uc import StoreBronzeUseCase
from src.domain.entities import (
    Category,
    ListingDetailsData,
    ParsedListingItem,
    PropertyType,
    RawEventMessage,
)
from src.infrastructure.database.mongo_bronze_repo import MongoBronzeRepository


@pytest.mark.asyncio
async def test_bronze_consumer_bulk_upsert():
    mock_collection = MagicMock()
    mock_bulk_result = MagicMock()
    mock_bulk_result.upserted_count = 2
    mock_bulk_result.modified_count = 0
    mock_collection.bulk_write = AsyncMock(return_value=mock_bulk_result)

    repo = MongoBronzeRepository.__new__(MongoBronzeRepository)
    repo.collection = mock_collection

    sample_event = RawEventMessage(
        listing_key="meeyland_111",
        site_id="meeyland",
        crawl_run_id="run_001",
        scraped_at=datetime.now(timezone.utc),
        parsed_data=ParsedListingItem(
            listing_key="meeyland_111",
            source="meeyland",
            category=Category.SALE,
            property_type=PropertyType.HOUSE,
            details=ListingDetailsData(url="https://test.com", title="Test"),
        ),
        raw_payload={"code": 111, "title": "Test"},
    )

    uc = StoreBronzeUseCase(bronze_repo=repo)
    count = await uc.process_batch([sample_event])

    assert count == 2
    assert mock_collection.bulk_write.called
    ops = mock_collection.bulk_write.call_args[0][0]
    assert len(ops) == 1
    # Check that $addToSet has crawl_run_id and $set has latest_raw_payload
    update_doc = ops[0]._doc["$set"]
    assert update_doc["site_id"] == "meeyland"
    assert ops[0]._doc["$addToSet"]["crawl_run_ids"] == "run_001"


@pytest.mark.asyncio
async def test_mongo_bronze_repo_close():
    mock_client = AsyncMock()
    repo = MongoBronzeRepository.__new__(MongoBronzeRepository)
    repo.client = mock_client

    await repo.close()
    mock_client.close.assert_awaited_once()


def test_init_mongo_indexes(monkeypatch):
    from src.infrastructure.database.mongo_setup import init_mongo_indexes

    mock_client_instance = MagicMock()
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_db.__getitem__.return_value = mock_collection
    mock_client_instance.__getitem__.return_value = mock_db

    monkeypatch.setattr(
        "src.infrastructure.database.mongo_setup.MongoClient",
        MagicMock(return_value=mock_client_instance),
    )

    init_mongo_indexes("mongodb://test:27017", "test_db")

    assert mock_collection.create_index.call_count == 3
    # Check that client.close() was called to prevent connection leaks
    assert mock_client_instance.close.called


@pytest.mark.asyncio
async def test_raw_event_consumer_service_with_dlq(monkeypatch):
    """Test RawEventConsumerService parses valid, forwards invalid to DLQ and commits."""
    from src.infrastructure.messaging.raw_event_consumer import RawEventConsumerService
    from aiokafka import TopicPartition

    service = RawEventConsumerService(batch_size=10, run_once=True)

    valid_payload = {
        "listing_key": "meeyland_123",
        "site_id": "meeyland",
        "crawl_run_id": "run_01",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "parsed_data": {
            "listing_key": "meeyland_123",
            "source": "meeyland",
            "category": "sale",
            "property_type": "house",
            "details": {"url": "https://meeyland.com/test", "title": "Test House"},
        },
        "raw_payload": {"id": 123},
    }

    mock_msg_valid = MagicMock(topic="raw.topic", partition=0, offset=10, key=b"meeyland_123", value=valid_payload)
    mock_msg_invalid = MagicMock(topic="raw.topic", partition=0, offset=11, key=b"bad_key", value={"invalid": "no listing_key"})

    tp = TopicPartition("raw.topic", 0)
    mock_consumer = AsyncMock()
    mock_consumer.start = AsyncMock()
    mock_consumer.stop = AsyncMock()
    mock_consumer.commit = AsyncMock()
    mock_consumer.seek = MagicMock()
    mock_consumer.getmany = AsyncMock(side_effect=[
        {tp: [mock_msg_valid, mock_msg_invalid]},
        {},
        {},
        {},
    ])

    mock_dlq = AsyncMock()
    mock_dlq.start = AsyncMock()
    mock_dlq.stop = AsyncMock()
    mock_dlq.publish = AsyncMock()

    mock_repo = AsyncMock()
    mock_repo.close = AsyncMock()

    mock_uc = AsyncMock()
    mock_uc.process_batch = AsyncMock(return_value=1)

    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.AIOKafkaConsumer", MagicMock(return_value=mock_consumer))
    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.AioKafkaEventPublisher", MagicMock(return_value=mock_dlq))
    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.MongoBronzeRepository", MagicMock(return_value=mock_repo))
    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.StoreBronzeUseCase", MagicMock(return_value=mock_uc))
    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.init_mongo_indexes", MagicMock())

    await service.run()

    assert mock_uc.process_batch.called
    assert mock_dlq.publish.called
    assert mock_consumer.commit.called
    assert not mock_consumer.seek.called


@pytest.mark.asyncio
async def test_raw_event_consumer_service_dlq_failure_rewinds(monkeypatch):
    """Test RawEventConsumerService rewinds offset when DLQ publish fails."""
    from src.infrastructure.messaging.raw_event_consumer import RawEventConsumerService
    from aiokafka import TopicPartition

    service = RawEventConsumerService(batch_size=10, run_once=True)

    mock_msg_invalid = MagicMock(topic="raw.topic", partition=0, offset=42, key=b"bad_key", value={"corrupt": True})

    tp = TopicPartition("raw.topic", 0)
    mock_consumer = AsyncMock()
    mock_consumer.start = AsyncMock()
    mock_consumer.stop = AsyncMock()
    mock_consumer.commit = AsyncMock()
    mock_consumer.seek = MagicMock()
    mock_consumer.getmany = AsyncMock(side_effect=[
        {tp: [mock_msg_invalid]},
        {},
        {},
        {},
    ])

    mock_dlq = AsyncMock()
    mock_dlq.start = AsyncMock()
    mock_dlq.stop = AsyncMock()
    mock_dlq.publish = AsyncMock(side_effect=RuntimeError("DLQ broker down"))

    mock_repo = AsyncMock()
    mock_repo.close = AsyncMock()

    mock_uc = AsyncMock()

    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.AIOKafkaConsumer", MagicMock(return_value=mock_consumer))
    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.AioKafkaEventPublisher", MagicMock(return_value=mock_dlq))
    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.MongoBronzeRepository", MagicMock(return_value=mock_repo))
    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.StoreBronzeUseCase", MagicMock(return_value=mock_uc))
    monkeypatch.setattr("src.infrastructure.messaging.raw_event_consumer.init_mongo_indexes", MagicMock())

    await service.run()

    assert mock_dlq.publish.called
    assert mock_consumer.seek.called
    # Verifies seek called with partition and offset 42
    seek_args = mock_consumer.seek.call_args[0]
    assert seek_args[0] == tp
    assert seek_args[1] == 42
    assert not mock_consumer.commit.called
