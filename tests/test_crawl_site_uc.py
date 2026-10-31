"""Unit tests for Spider and CrawlSiteUseCase."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.application.use_cases.crawl_site_uc import CrawlSiteUseCase
from src.domain.entities import Category, ListingDetailsData, ParsedListingItem, PropertyType, RunMode
from src.infrastructure.crawler.generic_api_spider import GenericApiSpider
from src.infrastructure.database.config_mapping import row_to_config
from src.infrastructure.database.postgres_crawl_run_repository import PostgresCrawlRunRepository


class MockSpider:
    def __init__(self, items, pages_crawled=1):
        self._items = items
        self.pages_crawled = pages_crawled

    async def crawl(self, run_mode):
        for item, raw in self._items:
            yield item, raw


@pytest.mark.asyncio
async def test_crawl_site_use_case_success(seed_row):
    config = row_to_config(seed_row)
    config_repo = AsyncMock()
    config_repo.get_config.return_value = config

    crawl_run_repo = AsyncMock()
    event_publisher = AsyncMock()

    sample_parsed = ParsedListingItem(
        listing_key="meeyland_123",
        source="meeyland",
        external_code=123,
        category=Category.SALE,
        property_type=PropertyType.HOUSE,
        details=ListingDetailsData(url="https://meeyland.com/123", title="Nhà đẹp"),
    )
    raw_payload = {"_id": "123", "title": "Nhà đẹp"}

    mock_spider = MockSpider(items=[(sample_parsed, raw_payload)], pages_crawled=2)

    uc = CrawlSiteUseCase(
        config_repo=config_repo,
        crawl_run_repo=crawl_run_repo,
        event_publisher=event_publisher,
        kafka_topic="realestate.raw_events",
    )

    run_id = await uc.execute(
        site_id="meeyland",
        run_mode=RunMode.INCREMENTAL,
        spider_override=mock_spider,
    )

    assert run_id.startswith("meeyland_")
    crawl_run_repo.abort_stale_runs.assert_awaited_once_with(site_id="meeyland", max_age_minutes=60)
    crawl_run_repo.create_run.assert_awaited_once()
    event_publisher.publish.assert_awaited_once()

    call_args = event_publisher.publish.call_args[1]
    assert call_args["topic"] == "realestate.raw_events"
    assert call_args["key"] == "meeyland_123"
    assert call_args["value"]["listing_key"] == "meeyland_123"

    # Verify pages_crawled metric was updated
    update_metrics_call = crawl_run_repo.update_metrics.call_args[1]
    assert update_metrics_call["pages_crawled"] == 2
    assert update_metrics_call["items_scraped"] == 1

    crawl_run_repo.complete_run.assert_awaited_with(
        crawl_run_id=run_id,
        status="completed",
    )
    config_repo.update_last_run.assert_awaited_once_with("meeyland")


@pytest.mark.asyncio
async def test_crawl_site_use_case_disabled_site(seed_row):
    seed_row["enabled"] = False
    config = row_to_config(seed_row)
    config_repo = AsyncMock()
    config_repo.get_config.return_value = config

    crawl_run_repo = AsyncMock()
    event_publisher = AsyncMock()

    uc = CrawlSiteUseCase(
        config_repo=config_repo,
        crawl_run_repo=crawl_run_repo,
        event_publisher=event_publisher,
    )

    run_id = await uc.execute("meeyland")
    assert run_id == ""
    crawl_run_repo.create_run.assert_not_called()


@pytest.mark.asyncio
async def test_spider_filters_disallowed_domains(seed_row):
    """Ensure spider skips listings whose host is not in allowed_domains and respects stop path."""
    config = row_to_config(seed_row)
    spider = GenericApiSpider(config)
    # Set 0 delay for fast test execution
    spider.rate_cfg.download_delay_sec = 0

    # Mock response: page 1 has items, page 2 has empty items
    mock_payload_p1 = {
        "data": {
            "results": [
                {
                    "_id": "item1",
                    "code": 111,
                    "title": "Nhà Meeyland",
                    "url": "https://meeyland.com/111",
                },
                {
                    "_id": "item2",
                    "code": 222,
                    "title": "Tin ngoài luồng",
                    "url": "https://malicious-spam-site.com/222",
                },
            ]
        }
    }
    mock_payload_p2 = {"data": {"results": []}}

    mock_resp_p1 = MagicMock()
    mock_resp_p1.status_code = 200
    mock_resp_p1.json.return_value = mock_payload_p1

    mock_resp_p2 = MagicMock()
    mock_resp_p2.status_code = 200
    mock_resp_p2.json.return_value = mock_payload_p2

    spider._send_request_with_retry = AsyncMock(side_effect=[mock_resp_p1, mock_resp_p2])

    crawled_items = []
    async for item, _ in spider.crawl(RunMode.INCREMENTAL):
        crawled_items.append(item)

    # Only item1 should pass the domain allowlist check, and spider stops when p2 is empty!
    assert len(crawled_items) == 1
    assert crawled_items[0].listing_key == "meeyland_111"
    assert spider.pages_crawled == 2


@pytest.mark.asyncio
async def test_postgres_crawl_run_repository_abort_stale_runs():
    mock_cursor = AsyncMock()
    mock_cursor.rowcount = 2

    mock_conn = AsyncMock()
    mock_conn.cursor = MagicMock()
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

    repo = PostgresCrawlRunRepository(pool=mock_pool)
    aborted = await repo.abort_stale_runs(site_id="meeyland", max_age_minutes=45)

    assert aborted == 2
    assert mock_cursor.execute.called
    query_str = mock_cursor.execute.call_args[0][0]
    assert "UPDATE crawl_runs" in query_str
    assert "status = 'failed'" in query_str
    params = mock_cursor.execute.call_args[0][1]
    assert params == ("45", "meeyland", "45")


@pytest.mark.asyncio
async def test_postgres_crawl_run_repository_update_metrics_greatest():
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

    repo = PostgresCrawlRunRepository(pool=mock_pool)
    await repo.update_metrics("run_123", items_scraped=50, pages_crawled=5)

    assert mock_conn.execute.called
    query_str = mock_conn.execute.call_args[0][0]
    assert "GREATEST(pages_crawled, %s)" in query_str
    assert mock_conn.execute.call_args[0][1] == (50, 5, "run_123")


@pytest.mark.asyncio
async def test_crawl_site_use_case_zero_items_warning(seed_row):
    """Ensure zero scraped items produces warning in crawl_runs instead of silent success."""
    config = row_to_config(seed_row)
    config_repo = AsyncMock()
    config_repo.get_config.return_value = config

    crawl_run_repo = AsyncMock()
    event_publisher = AsyncMock()

    mock_spider = MockSpider(items=[], pages_crawled=1)

    uc = CrawlSiteUseCase(
        config_repo=config_repo,
        crawl_run_repo=crawl_run_repo,
        event_publisher=event_publisher,
        kafka_topic="realestate.raw_events",
    )

    run_id = await uc.execute(
        site_id="meeyland",
        run_mode=RunMode.INCREMENTAL,
        spider_override=mock_spider,
    )

    assert run_id.startswith("meeyland_")
    crawl_run_repo.complete_run.assert_awaited_with(
        crawl_run_id=run_id,
        status="completed",
        error_message="Warning: 0 items scraped for site 'meeyland'. Possible layout change or bot block.",
    )

