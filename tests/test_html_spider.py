"""Unit tests for GenericHtmlSpider and HTML-based crawler configurations."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
import httpx

from src.domain.entities import Category, PropertyType, RunMode
from src.domain.entities.crawler_config import CrawlerConfig
from src.infrastructure.crawler.generic_html_spider import GenericHtmlSpider
from src.infrastructure.crawler.spider_factory import create_spider


SAMPLE_LIST_HTML = """
<!DOCTYPE html>
<html>
<body>
    <div class="props-item" data-id="101">
        <h2 class="prop-title"><a href="/quan-1/ban-nha-pho/nha-dep-id101">Nhà đẹp mặt tiền Quận 1</a></h2>
        <span class="price">5,2 tỷ</span>
        <span class="square">100 m²</span>
        <div class="prop-addr">Phường Bến Nghé, Quận 1, TP Hồ Chí Minh</div>
        <img class="thumb" src="/images/thumb101.jpg" />
    </div>
    <div class="props-item" data-id="102">
        <h2 class="prop-title"><a href="/quan-2/ban-dat/dat-nen-id102">Đất nền An Phú</a></h2>
        <span class="price">45 tr/m²</span>
        <span class="square">80 m²</span>
        <div class="prop-addr">Phường An Phú, Quận 2, TP Hồ Chí Minh</div>
        <img class="thumb" src="/images/thumb102.jpg" />
    </div>
</body>
</html>
"""

SAMPLE_DETAIL_HTML = """
<!DOCTYPE html>
<html>
<body>
    <div class="agent-info">
        <div class="agent-name">
            <a href="/moi-gioi/0912345678-nguyen-van-a-uid888">Nguyễn Văn A</a>
        </div>
    </div>
    <div class="property-description">
        Nhà chính chủ, sổ hồng riêng, đường trước nhà 6m.
    </div>
    <div class="media-gallery">
        <img src="/photos/photo1.jpg" />
        <img src="/photos/photo2.jpg" />
    </div>
</body>
</html>
"""


@pytest.fixture
def mogi_html_config() -> CrawlerConfig:
    data = {
        "site_id": "mogi",
        "name": "Mogi - Mua bán BĐS",
        "enabled": True,
        "crawler_type": "html",
        "schedule_times": [],
        "interval_seconds": 300,
        "request": {
            "url": "https://mogi.vn/mua-nha-dat",
            "method": "GET",
            "headers": {"User-Agent": "TestBot"},
            "query_params": {},
        },
        "pagination": {
            "strategy": "page_number",
            "page_param": "cp",
            "start_page": 1,
            "page_size": 20,
            "max_pages_incremental": 1,
            "max_pages_full_sweep": 5,
        },
        "rate_limit": {
            "download_delay_sec": 0.01,
            "timeout_sec": 5.0,
            "retry_times": 1,
        },
        "response_mapping": {
            "items_path": "div.props-item",
            "fields": {
                "external_id": "div.props-item::attr(data-id)",
                "url": "h2.prop-title a::attr(href)",
                "title": "h2.prop-title a::text",
                "price_vnd": "span.price::text",
                "area_m2": "span.square::text",
                "city_name": "div.prop-addr::text",
                "thumbnail_url": "img.thumb::attr(src)",
            },
        },
        "detail_page": {
            "enabled": True,
            "url_field": "url",
            "fields": {
                "advertiser_phone": "div.agent-name a::attr(href)",
                "advertiser_name": "div.agent-name a::text",
                "description": "div.property-description::text",
                "media_list": "div.media-gallery img::attr(src)",
            },
        },
    }
    return CrawlerConfig.model_validate(data)


@pytest.mark.asyncio
async def test_spider_factory_creates_html_spider(mogi_html_config):
    spider = create_spider(mogi_html_config)
    assert isinstance(spider, GenericHtmlSpider)


@pytest.mark.asyncio
async def test_generic_html_spider_crawl(mogi_html_config, monkeypatch):
    spider = GenericHtmlSpider(mogi_html_config)

    # Mock HTTP client responses for list page and detail page
    mock_list_response = MagicMock(status_code=200, text=SAMPLE_LIST_HTML, url=httpx.URL("https://mogi.vn/mua-nha-dat"))
    mock_detail_response = MagicMock(status_code=200, text=SAMPLE_DETAIL_HTML, url=httpx.URL("https://mogi.vn/quan-1/ban-nha-pho/nha-dep-id101"))

    async def mock_get(url, params=None, timeout=None):
        if "mua-nha-dat" in str(url):
            return mock_list_response
        return mock_detail_response

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=mock_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock()

    monkeypatch.setattr("httpx.AsyncClient", MagicMock(return_value=mock_client))

    items = []
    async for item, raw in spider.crawl(RunMode.INCREMENTAL):
        items.append(item)

    assert len(items) == 2
    first = items[0]
    assert first.listing_key == "mogi_101"
    assert first.details.title == "Nhà đẹp mặt tiền Quận 1"
    assert first.current_price_vnd == 5_200_000_000.0
    assert first.area_m2 == 100.0
    assert first.current_price_per_m2 == 52_000_000.0
    assert first.details.url == "https://mogi.vn/quan-1/ban-nha-pho/nha-dep-id101"
    assert first.details.thumbnail_url == "https://mogi.vn/images/thumb101.jpg"
    # Detail page deep-scraped fields
    assert first.advertiser.phone == "0912345678"
    assert first.advertiser.name == "Nguyễn Văn A"
    assert first.advertiser.advertiser_id == "adv_mogi_0912345678"
    assert first.details.legal_status == "Sổ đỏ/hồng"
    assert first.details.media_list == [
        "https://mogi.vn/photos/photo1.jpg",
        "https://mogi.vn/photos/photo2.jpg",
    ]

    # Second item with price per m2: 45 tr/m2 * 80m2 = 3.6 tỷ
    second = items[1]
    assert second.listing_key == "mogi_102"
    assert second.area_m2 == 80.0
    assert second.current_price_per_m2 == 45_000_000.0
    assert second.current_price_vnd == 3_600_000_000.0


def test_extract_selector_value_with_xpath_functions(mogi_html_config):
    from parsel import Selector
    spider = GenericHtmlSpider(mogi_html_config)

    html = """
    <div class="container">
        <div class="info-attr"><span>Ngày đăng</span><span>31/10/2026</span></div>
        <div class="info-attr"><span>Pháp lý</span><span>Sổ hồng</span></div>
        <span class="fallback-date">01/01/2026</span>
    </div>
    """
    sel = Selector(text=html)
    xpath_selector = "//div[contains(@class, 'info-attr')][span[contains(text(), 'Ngày đăng')]]/span[2]/text(), span.fallback-date::text"
    val = spider._extract_selector_value(sel, xpath_selector)
    assert val == "31/10/2026"


