"""Unit tests for messaging services (Raw, DLQ, Clean) and Crawler Scheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.domain.entities import Category, ListingDetailsData, ParsedListingItem, PropertyType, RawEventMessage, RunMode
from src.infrastructure.messaging.dlq_event_consumer import DLQEventConsumerService
from src.infrastructure.messaging.clean_event_consumer import CleanEventProcessorService
from src.infrastructure.crawler.crawler_scheduler_entrypoint import CrawlerSchedulerService
from src.infrastructure.database.mongo_setup import init_dlq_mongo_indexes


@pytest.mark.asyncio
async def test_dlq_event_consumer_run_once(monkeypatch):
    """Test DLQ consumer processes batch and commits offset in run_once mode."""
    service = DLQEventConsumerService(batch_size=5, flush_interval_sec=0.1, run_once=True)

    mock_collection = MagicMock()
    mock_collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["1", "2"]))
    mock_collection.bulk_write = AsyncMock(return_value=MagicMock(upserted_count=2, modified_count=0))
    mock_db = MagicMock()
    mock_db.__getitem__.return_value = mock_collection
    mock_mongo_client = AsyncMock()
    mock_mongo_client.__getitem__.return_value = mock_db
    mock_mongo_client.close = AsyncMock()

    mock_msg1 = MagicMock(topic="test.dlq", partition=0, offset=1, key=b"key1", value={"error": "err1"})
    mock_msg2 = MagicMock(topic="test.dlq", partition=0, offset=2, key="key2", value="non-dict error")

    mock_consumer = AsyncMock()
    mock_consumer.start = AsyncMock()
    mock_consumer.stop = AsyncMock()
    mock_consumer.commit = AsyncMock()
    # First poll returns 2 messages, next polls return empty dict to trigger exit in run_once mode
    mock_consumer.getmany = AsyncMock(side_effect=[
        {("test.dlq", 0): [mock_msg1, mock_msg2]},
        {},
        {},
        {},
    ])

    monkeypatch.setattr("src.infrastructure.messaging.dlq_event_consumer.AsyncMongoClient", MagicMock(return_value=mock_mongo_client))
    monkeypatch.setattr("src.infrastructure.messaging.dlq_event_consumer.AIOKafkaConsumer", MagicMock(return_value=mock_consumer))
    monkeypatch.setattr("src.infrastructure.messaging.dlq_event_consumer.init_dlq_mongo_indexes", MagicMock())

    await service.run()

    assert mock_consumer.start.called
    assert mock_collection.bulk_write.called or mock_collection.insert_many.called
    assert mock_consumer.commit.called
    assert mock_mongo_client.close.called
    if mock_collection.bulk_write.called:
        ops = mock_collection.bulk_write.call_args[0][0]
        assert len(ops) == 2
        doc0 = ops[0]._doc["$setOnInsert"]
        assert doc0["error"] == "err1"
        assert doc0["kafka_topic"] == "test.dlq"
        assert doc0["_id"] == "test.dlq_0_1"
    else:
        inserted_batch = mock_collection.insert_many.call_args[0][0]
        assert len(inserted_batch) == 2


def test_init_dlq_mongo_indexes(monkeypatch):
    """Test creating indexes on dlq_events collection."""
    mock_client_instance = MagicMock()
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_db.__getitem__.return_value = mock_collection
    mock_client_instance.__getitem__.return_value = mock_db

    monkeypatch.setattr(
        "src.infrastructure.database.mongo_setup.MongoClient",
        MagicMock(return_value=mock_client_instance),
    )

    init_dlq_mongo_indexes("mongodb://test:27017", "test_db")

    assert mock_collection.create_index.call_count == 2
    assert mock_client_instance.close.called


@pytest.mark.asyncio
async def test_clean_event_processor_run_once(monkeypatch):
    """Test clean event processor runs batch, updates metrics, and commits offset."""
    service = CleanEventProcessorService(batch_size=5, run_once=True)

    mock_dlq_publisher = AsyncMock()
    mock_dlq_publisher.start = AsyncMock()
    mock_dlq_publisher.stop = AsyncMock()
    mock_dlq_publisher.publish = AsyncMock()

    sample_event = RawEventMessage(
        listing_key="clean_test_1",
        site_id="meeyland",
        crawl_run_id="run_100",
        scraped_at=datetime.now(timezone.utc),
        parsed_data=ParsedListingItem(
            listing_key="clean_test_1",
            source="meeyland",
            category=Category.SALE,
            property_type=PropertyType.HOUSE,
            details=ListingDetailsData(url="https://test.com", title="Test House"),
        ),
        raw_payload={"id": 1},
    )

    valid_msg = MagicMock(topic="raw.topic", partition=0, offset=10, key=b"clean_test_1", value=sample_event.model_dump(mode="json"))
    corrupt_msg = MagicMock(topic="raw.topic", partition=0, offset=11, key=b"bad_key", value={"invalid": "payload"})

    mock_consumer = AsyncMock()
    mock_consumer.start = AsyncMock()
    mock_consumer.stop = AsyncMock()
    mock_consumer.commit = AsyncMock()
    mock_consumer.getmany = AsyncMock(side_effect=[
        {("raw.topic", 0): [valid_msg, corrupt_msg]},
        {},
        {},
        {},
    ])

    mock_repo = AsyncMock()
    mock_repo.batch_update_crawl_run_metrics = AsyncMock()

    mock_use_case = AsyncMock()
    mock_use_case.execute = AsyncMock(return_value=(True, False))
    mock_use_case.execute_batch = AsyncMock(return_value=[(sample_event, True, False)])

    monkeypatch.setattr("src.infrastructure.messaging.clean_event_consumer.AioKafkaEventPublisher", MagicMock(return_value=mock_dlq_publisher))
    monkeypatch.setattr("src.infrastructure.messaging.clean_event_consumer.AIOKafkaConsumer", MagicMock(return_value=mock_consumer))
    monkeypatch.setattr("src.infrastructure.messaging.clean_event_consumer.PgListingRepository", MagicMock(return_value=mock_repo))
    monkeypatch.setattr("src.infrastructure.messaging.clean_event_consumer.ProcessSilverGoldUseCase", MagicMock(return_value=mock_use_case))

    # Mock AsyncConnectionPool context manager
    mock_cur = MagicMock()
    mock_cur.rowcount = 0
    mock_cur.execute = AsyncMock()
    mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur.__aexit__ = AsyncMock(return_value=None)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.__aenter__ = AsyncMock(return_value=mock_pool)
    mock_pool.__aexit__ = AsyncMock(return_value=None)
    mock_pool.connection = MagicMock(return_value=mock_conn)
    monkeypatch.setattr("psycopg_pool.AsyncConnectionPool", MagicMock(return_value=mock_pool))

    await service.run()

    assert mock_use_case.execute_batch.called or mock_use_case.execute.called
    assert mock_dlq_publisher.publish.called  # corrupt_msg forwarded to DLQ
    assert mock_repo.batch_update_crawl_run_metrics.called
    assert mock_consumer.commit.called
    assert mock_consumer.stop.called


@pytest.mark.asyncio
async def test_crawler_scheduler_due_sites_logic(monkeypatch):
    """Test scheduler accurately detects due vs non-due and already running sites."""
    scheduler = CrawlerSchedulerService(poll_interval_sec=0.1, run_once=True)

    now = datetime.now(timezone.utc)
    configs = [
        # Site 1: Never ran before -> due immediately
        {
            "site_id": "site_new",
            "name": "Site New",
            "enabled": True,
            "interval_seconds": 300,
            "schedule_times": [],
            "last_run_at": None,
        },
        # Site 2: Ran recently (50s ago, interval 300) -> not due
        {
            "site_id": "site_recent",
            "name": "Site Recent",
            "enabled": True,
            "interval_seconds": 300,
            "schedule_times": [],
            "last_run_at": now - timedelta(seconds=50),
        },
        # Site 3: Ran 400s ago, interval 300 -> due
        {
            "site_id": "site_due",
            "name": "Site Due",
            "enabled": True,
            "interval_seconds": 300,
            "schedule_times": [],
            "last_run_at": now - timedelta(seconds=400),
        },
        # Site 4: Due by interval, but already running -> must be skipped
        {
            "site_id": "site_running",
            "name": "Site Running",
            "enabled": True,
            "interval_seconds": 60,
            "schedule_times": [],
            "last_run_at": now - timedelta(seconds=120),
        },
    ]

    mock_pool = MagicMock()
    # Mock _get_active_running_sites
    scheduler._get_active_running_sites = AsyncMock(return_value={"site_running"})

    # Mock DB query in _get_due_sites
    mock_cur = AsyncMock()
    mock_cur.fetchall = AsyncMock(return_value=configs)
    mock_conn = AsyncMock()
    mock_conn.cursor = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_cur), __aexit__=AsyncMock()))
    mock_pool.connection = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

    due = await scheduler._get_due_sites(mock_pool)
    due_site_ids = [d["site_id"] for d in due]

    assert "site_new" in due_site_ids
    assert "site_due" in due_site_ids
    assert "site_recent" not in due_site_ids
    assert "site_running" not in due_site_ids


@pytest.mark.asyncio
async def test_crawler_scheduler_run_once_execution(monkeypatch):
    """Test full scheduler run loop in run_once mode triggers crawl use case."""
    scheduler = CrawlerSchedulerService(poll_interval_sec=0.1, run_once=True)

    due_configs = [{"site_id": "meeyland", "mode": RunMode.INCREMENTAL, "name": "Meeyland"}]
    scheduler._get_due_sites = AsyncMock(return_value=due_configs)

    mock_publisher = AsyncMock()
    mock_publisher.start = AsyncMock()
    mock_publisher.stop = AsyncMock()
    monkeypatch.setattr("src.infrastructure.crawler.crawler_scheduler_entrypoint.AioKafkaEventPublisher", MagicMock(return_value=mock_publisher))

    mock_use_case = AsyncMock()
    mock_use_case.execute = AsyncMock(return_value="meeyland_run_123")
    monkeypatch.setattr("src.infrastructure.crawler.crawler_scheduler_entrypoint.CrawlSiteUseCase", MagicMock(return_value=mock_use_case))

    mock_cur = MagicMock()
    mock_cur.rowcount = 0
    mock_cur.execute = AsyncMock()
    mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur.__aexit__ = AsyncMock(return_value=None)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.__aenter__ = AsyncMock(return_value=mock_pool)
    mock_pool.__aexit__ = AsyncMock(return_value=None)
    mock_pool.connection = MagicMock(return_value=mock_conn)
    monkeypatch.setattr("psycopg_pool.AsyncConnectionPool", MagicMock(return_value=mock_pool))

    await scheduler.run()

    assert mock_publisher.start.called
    assert mock_use_case.execute_batch.called or mock_use_case.execute.called
    assert mock_publisher.stop.called

