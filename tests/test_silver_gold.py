"""Unit tests for Silver & Gold Consumer transactional ingestion."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.application.use_cases.process_silver_gold_uc import ProcessSilverGoldUseCase
from src.domain.entities import (
    AdvertiserData,
    AdvertiserKind,
    Category,
    ListingDetailsData,
    ParsedListingItem,
    PropertyType,
    RawEventMessage,
)
from src.infrastructure.database.pg_listing_repo import PgListingRepository


def make_mock_pool(mock_cursor):
    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock()
    mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_conn.cursor = MagicMock()
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_pool


@pytest.fixture
def sample_item():
    return ParsedListingItem(
        listing_key="meeyland_555",
        source="meeyland",
        external_code=555,
        category=Category.SALE,
        property_type=PropertyType.HOUSE,
        current_price_vnd=3000000000.0,
        area_m2=50.0,
        current_price_per_m2=60000000.0,
        details=ListingDetailsData(url="https://test.com/555", title="Nhà phố"),
        advertiser=AdvertiserData(advertiser_id="adv_1", phone="0901234567", kind=AdvertiserKind.BROKER),
    )


@pytest.mark.asyncio
async def test_process_silver_gold_new_listing(sample_item):
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = None

    mock_pool = make_mock_pool(mock_cursor)
    repo = PgListingRepository(pool=mock_pool)
    uc = ProcessSilverGoldUseCase(pg_repo=repo)

    event = RawEventMessage(
        listing_key="meeyland_555",
        site_id="meeyland",
        crawl_run_id="run_1",
        parsed_data=sample_item,
        raw_payload={},
    )

    is_new, price_changed = await uc.execute(event)

    assert is_new is True
    assert price_changed is False
    assert mock_cursor.execute.call_count >= 3  # advertiser, listing, details, history, metrics


@pytest.mark.asyncio
async def test_process_silver_gold_price_changed(sample_item):
    mock_cursor = AsyncMock()
    # Existing listing with lower price (2.5 tỷ -> now 3.0 tỷ)
    mock_cursor.fetchone.return_value = {
        "current_price_vnd": 2500000000.0,
        "current_price_per_m2": 50000000.0,
        "status": "active",
    }

    mock_pool = make_mock_pool(mock_cursor)
    repo = PgListingRepository(pool=mock_pool)
    uc = ProcessSilverGoldUseCase(pg_repo=repo)

    event = RawEventMessage(
        listing_key="meeyland_555",
        site_id="meeyland",
        crawl_run_id="run_1",
        parsed_data=sample_item,
        raw_payload={},
    )

    is_new, price_changed = await uc.execute(event)

    assert is_new is False
    assert price_changed is True



@pytest.mark.asyncio
async def test_process_silver_gold_contact_to_priced(sample_item):
    """Ensure listing transitioning from 'contact' (NULL price) to a numeric price is recorded."""
    mock_cursor = AsyncMock()
    # Existing listing had NO price (price_status = 'contact', current_price_vnd = None)
    mock_cursor.fetchone.return_value = {
        "current_price_vnd": None,
        "current_price_per_m2": None,
        "status": "active",
    }

    mock_pool = make_mock_pool(mock_cursor)
    repo = PgListingRepository(pool=mock_pool)
    uc = ProcessSilverGoldUseCase(pg_repo=repo)

    event = RawEventMessage(
        listing_key="meeyland_555",
        site_id="meeyland",
        crawl_run_id="run_1",
        parsed_data=sample_item,  # has current_price_vnd = 3_000_000_000
        raw_payload={},
    )

    is_new, price_changed = await uc.execute(event)

    assert is_new is False
    assert price_changed is True


@pytest.mark.asyncio
async def test_process_silver_gold_priced_to_contact(sample_item):
    """Ensure listing transitioning from numeric price to 'contact' (agreement) sets price NULL."""
    mock_cursor = AsyncMock()
    # Existing listing had price 3.0 tỷ
    mock_cursor.fetchone.return_value = {
        "current_price_vnd": 3000000000.0,
        "current_price_per_m2": 60000000.0,
        "status": "active",
    }

    mock_pool = make_mock_pool(mock_cursor)
    repo = PgListingRepository(pool=mock_pool)
    uc = ProcessSilverGoldUseCase(pg_repo=repo)

    # Incoming item has price = None (Giá thỏa thuận)
    sample_item.current_price_vnd = None
    sample_item.current_price_per_m2 = None

    event = RawEventMessage(
        listing_key="meeyland_555",
        site_id="meeyland",
        crawl_run_id="run_1",
        parsed_data=sample_item,
        raw_payload={},
    )

    is_new, price_changed = await uc.execute(event)

    assert is_new is False
    assert price_changed is True
    # Ensure UPDATE listings setting price_status = 'contact' and current_price_vnd = NULL was called
    executed_calls = mock_cursor.execute.call_args_list
    executed_queries = [c[0][0] for c in executed_calls]
    contact_calls = [c for c in executed_calls if "UPDATE listings SET" in c[0][0]]
    assert len(contact_calls) == 1
    # Verify that 'contact' was passed in params
    params = contact_calls[0][0][1]
    assert "contact" in params

    # Ensure UPDATE listing_details was called for existing listing
    details_updates = [q for q in executed_queries if "UPDATE listing_details" in q]
    assert len(details_updates) == 1


@pytest.mark.asyncio
async def test_batch_update_crawl_run_metrics():
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

    repo = PgListingRepository(pool=mock_pool)
    await repo.batch_update_crawl_run_metrics(
        crawl_run_id="run_1",
        items_processed=50,
        new_listings_count=10,
        price_changes_detected=5,
    )

    assert mock_conn.execute.called
    call_args = mock_conn.execute.call_args
    assert "UPDATE crawl_runs SET" in call_args[0][0]
    assert call_args[0][1] == (50, 10, 5, "run_1")


@pytest.mark.asyncio
async def test_process_silver_gold_reactivates_expired_listing(sample_item):
    """Ensure previously expired listing has ended_at reset to NULL upon reactivation."""
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = {
        "current_price_vnd": 3000000000.0,
        "current_price_per_m2": 60000000.0,
        "status": "expired",
    }

    mock_pool = make_mock_pool(mock_cursor)
    repo = PgListingRepository(pool=mock_pool)
    uc = ProcessSilverGoldUseCase(pg_repo=repo)

    event = RawEventMessage(
        listing_key="meeyland_555",
        site_id="meeyland",
        crawl_run_id="run_1",
        parsed_data=sample_item,
        raw_payload={},
    )

    is_new, price_changed = await uc.execute(event)

    assert is_new is False
    assert price_changed is False

    executed_queries = [call[0][0] for call in mock_cursor.execute.call_args_list]
    reactivate_updates = [
        q for q in executed_queries
        if "ended_at = NULL" in q and "status = 'active'" in q and "advertiser_id = COALESCE(%s, advertiser_id)" in q
    ]
    assert len(reactivate_updates) == 1


@pytest.mark.asyncio
async def test_process_silver_gold_updates_physical_attributes_and_uses_distinct_from(sample_item):
    """Ensure existing listings update physical attributes and listing_details uses IS DISTINCT FROM."""
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = {
        "current_price_vnd": 3000000000.0,
        "current_price_per_m2": 60000000.0,
        "status": "active",
    }

    mock_pool = make_mock_pool(mock_cursor)
    repo = PgListingRepository(pool=mock_pool)
    uc = ProcessSilverGoldUseCase(pg_repo=repo)

    # Change property type to villa and area to 85m2
    sample_item.property_type = PropertyType.VILLA
    sample_item.area_m2 = 85.0
    sample_item.facade_m = 6.0
    sample_item.depth_m = 14.0

    event = RawEventMessage(
        listing_key="meeyland_555",
        site_id="meeyland",
        crawl_run_id="run_1",
        parsed_data=sample_item,
        raw_payload={},
    )

    is_new, price_changed = await uc.execute(event, update_crawl_run=False)

    assert is_new is False
    assert price_changed is False

    executed_queries = [call[0][0] for call in mock_cursor.execute.call_args_list]

    # Verify physical attributes are included in unchanged price update
    listing_updates = [q for q in executed_queries if "UPDATE listings SET" in q]
    assert len(listing_updates) == 1
    assert "property_type = %s" in listing_updates[0]
    assert "area_m2 = COALESCE(%s, area_m2)" in listing_updates[0]

    # Verify listing_details uses IS DISTINCT FROM to avoid dead tuples
    details_updates = [q for q in executed_queries if "UPDATE listing_details SET" in q]
    assert len(details_updates) == 1
    assert "IS DISTINCT FROM" in details_updates[0]

    # Verify crawl_runs was NOT updated because update_crawl_run=False
    crawl_run_updates = [q for q in executed_queries if "UPDATE crawl_runs SET" in q]
    assert len(crawl_run_updates) == 0


@pytest.mark.asyncio
async def test_kafka_publisher_publish_batch():
    """Ensure AioKafkaEventPublisher.publish_batch sends messages concurrently."""
    from src.infrastructure.kafka.producer import AioKafkaEventPublisher

    publisher = AioKafkaEventPublisher(bootstrap_servers="localhost:9092")
    mock_producer = AsyncMock()
    publisher._producer = mock_producer

    events = [
        ("key_1", {"data": 1}),
        ("key_2", {"data": 2}),
    ]

    await publisher.publish_batch(topic="test.topic", events=events)

    assert mock_producer.send.call_count == 2
    calls = mock_producer.send.call_args_list
    assert calls[0][1] == {"topic": "test.topic", "key": "key_1", "value": {"data": 1}}
    assert calls[1][1] == {"topic": "test.topic", "key": "key_2", "value": {"data": 2}}


@pytest.mark.asyncio
async def test_process_silver_gold_execute_batch(sample_item):
    """Ensure ProcessSilverGoldUseCase.execute_batch runs multiple items in single batch."""
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = None

    mock_pool = make_mock_pool(mock_cursor)
    repo = PgListingRepository(pool=mock_pool)
    uc = ProcessSilverGoldUseCase(pg_repo=repo)

    events = [
        RawEventMessage(
            listing_key="meeyland_555",
            site_id="meeyland",
            crawl_run_id="run_1",
            parsed_data=sample_item,
            raw_payload={},
        ),
        RawEventMessage(
            listing_key="meeyland_556",
            site_id="meeyland",
            crawl_run_id="run_1",
            parsed_data=sample_item,
            raw_payload={},
        ),
    ]

    results = await uc.execute_batch(events)
    assert len(results) == 2
    assert results[0][1] is True  # is_new
    assert results[1][1] is True  # is_new
    assert mock_pool.connection.call_count == 1



