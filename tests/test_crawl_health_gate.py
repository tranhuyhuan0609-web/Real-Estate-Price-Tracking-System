"""Tests for the crawl health gate, lifecycle safety gate and Kafka delivery awaiting."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.apply_lifecycle_uc import ApplyLifecycleUseCase
from src.application.use_cases.crawl_site_uc import CrawlSiteUseCase
from src.domain.entities import Category, ListingDetailsData, ParsedListingItem, PropertyType, RunMode
from src.infrastructure.database.config_mapping import row_to_config
from src.infrastructure.kafka.producer import AioKafkaEventPublisher
from tests.test_lifecycle_uc import make_mock_pool


class FlaggedSpider:
    """Spider double exposing the health signals read by CrawlSiteUseCase."""

    def __init__(self, items, aborted_reason=None, exhausted=True):
        self._items = items
        self.pages_crawled = 1
        self.aborted_reason = aborted_reason
        self.exhausted = exhausted

    async def crawl(self, run_mode):
        for item, raw in self._items:
            yield item, raw


def _item():
    parsed = ParsedListingItem(
        listing_key="meeyland_1",
        source="meeyland",
        external_code=1,
        category=Category.SALE,
        property_type=PropertyType.HOUSE,
        details=ListingDetailsData(url="https://meeyland.com/1", title="Nhà"),
    )
    return parsed, {"_id": "1"}


async def _run(seed_row, spider, mode):
    config_repo = AsyncMock()
    config_repo.get_config.return_value = row_to_config(seed_row)
    crawl_run_repo = AsyncMock()
    uc = CrawlSiteUseCase(config_repo, crawl_run_repo, AsyncMock())
    run_id = await uc.execute("meeyland", run_mode=mode, spider_override=spider)
    return run_id, crawl_run_repo


@pytest.mark.asyncio
async def test_aborted_full_sweep_is_marked_failed(seed_row):
    spider = FlaggedSpider([_item()], aborted_reason="blocked", exhausted=False)
    run_id, repo = await _run(seed_row, spider, RunMode.FULL_SWEEP)
    kwargs = repo.complete_run.call_args[1]
    assert kwargs["status"] == "failed"
    assert "blocked" in kwargs["error_message"]


@pytest.mark.asyncio
async def test_page_cap_truncated_full_sweep_is_marked_failed(seed_row):
    spider = FlaggedSpider([_item()], aborted_reason=None, exhausted=False)
    _, repo = await _run(seed_row, spider, RunMode.FULL_SWEEP)
    assert repo.complete_run.call_args[1]["status"] == "failed"


@pytest.mark.asyncio
async def test_empty_full_sweep_is_marked_failed(seed_row):
    spider = FlaggedSpider([], exhausted=True)
    _, repo = await _run(seed_row, spider, RunMode.FULL_SWEEP)
    assert repo.complete_run.call_args[1]["status"] == "failed"


@pytest.mark.asyncio
async def test_clean_full_sweep_is_completed(seed_row):
    spider = FlaggedSpider([_item()], exhausted=True)
    run_id, repo = await _run(seed_row, spider, RunMode.FULL_SWEEP)
    repo.complete_run.assert_awaited_with(crawl_run_id=run_id, status="completed")


@pytest.mark.asyncio
async def test_aborted_incremental_keeps_completed_with_warning(seed_row):
    spider = FlaggedSpider([_item()], aborted_reason="blocked", exhausted=False)
    _, repo = await _run(seed_row, spider, RunMode.INCREMENTAL)
    kwargs = repo.complete_run.call_args[1]
    assert kwargs["status"] == "completed"
    assert "blocked" in kwargs["error_message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, items_scraped",
    [("failed", 100), ("completed", 0)],
)
async def test_lifecycle_skips_unhealthy_run(status, items_scraped):
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = {
        "crawl_run_id": "run_x",
        "site_id": "meeyland",
        "run_mode": "full_sweep",
        "status": status,
        "started_at": datetime(2026, 10, 30, tzinfo=timezone.utc),
        "lifecycle_applied_at": None,
        "items_scraped": items_scraped,
    }
    uc = ApplyLifecycleUseCase(pool=make_mock_pool(mock_cursor))
    result = await uc.execute("run_x")

    assert result == {"incremented": 0, "expired": 0}
    assert mock_cursor.execute.call_count == 1  # only the SELECT, no UPDATE


@pytest.mark.asyncio
async def test_wait_for_ingestion_returns_true_when_all_processed():
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = (100, 100)
    uc = ApplyLifecycleUseCase(pool=make_mock_pool(mock_cursor))
    assert await uc.wait_for_ingestion("run_x", poll_sec=0.01) is True


@pytest.mark.asyncio
async def test_wait_for_ingestion_rejects_stalled_low_progress():
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = (100, 10)
    uc = ApplyLifecycleUseCase(pool=make_mock_pool(mock_cursor))
    assert await uc.wait_for_ingestion("run_x", idle_sec=0.05, poll_sec=0.01) is False


@pytest.mark.asyncio
async def test_publish_batch_awaits_delivery_futures():
    """publish_batch must surface broker delivery errors instead of dropping them."""
    publisher = AioKafkaEventPublisher(bootstrap_servers="localhost:9092")
    failing = asyncio.get_running_loop().create_future()
    failing.set_exception(RuntimeError("broker rejected"))

    producer = MagicMock()
    producer.send = AsyncMock(return_value=failing)
    publisher._producer = producer

    with pytest.raises(RuntimeError, match="broker rejected"):
        await publisher.publish_batch("t", [("k", {"v": 1})])
