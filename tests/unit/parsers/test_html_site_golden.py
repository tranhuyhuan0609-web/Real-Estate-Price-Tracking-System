"""Golden tests: production YAML configs + GenericHtmlSpider against captured (anonymised) pages.

A failure here after changing a selector means the config no longer reads the real layout. When
a site changes its layout, re-capture with scripts/capture_site_fixture.py and update EXPECTED.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.unit.parsers.html_golden_harness import crawl_fixture

UTC = timezone.utc
VN = ZoneInfo("Asia/Ho_Chi_Minh")  # content_timezone: dates on the page are local midnight

# (listing_key, area_m2, price_vnd) of every card, in page order
EXPECTED = {
    "mogi": [
        ("mogi_22751689", 91.0, 83_000_000_000),
        ("mogi_22595305", 250.0, 53_000_000_000),
        ("mogi_22599559", 284.0, 32_000_000_000),
    ],
    "homedy": [
        ("homedy_3225661", 114.0, 9_800_000_000),
        ("homedy_1490980", 59.0, 2_390_000_000),
    ],
    "cafeland": [
        ("cafeland_3195376", 108.0, 8_800_000_000),
        ("cafeland_3176122", 108.0, 8_900_000_000),
        ("cafeland_3194057", 264.0, 8_800_000_000),
    ],
}

# Fields that only the detail page provides (first card only)
EXPECTED_DETAIL = {
    "mogi": dict(
        published_at=datetime(2026, 11, 4, tzinfo=VN),
        phone="0900000001",
        media_min=10,
        city="Thành phố Hồ Chí Minh",
        district="Quận 1",
    ),
    "homedy": dict(
        published_at=datetime(2026, 11, 2, tzinfo=VN),
        phone="0900000001",
        media_min=0,
        city="Thành phố Đà Nẵng",
        district="Quận Sơn Trà",
    ),
    "cafeland": dict(
        published_at=datetime(2026, 11, 3, tzinfo=VN),
        phone="0900000007",
        media_min=0,
        city="Thành phố Cần Thơ",
        district="Cái Răng",
    ),
}


@pytest.mark.parametrize("site_id", sorted(EXPECTED))
async def test_list_page_cards(site_id, monkeypatch):
    items, spider = await crawl_fixture(site_id, monkeypatch)

    assert [(i.listing_key, i.area_m2, i.current_price_vnd) for i in items] == EXPECTED[site_id]
    assert spider.exhausted and spider.aborted_reason is None  # empty page 2 = clean end
    for item in items:
        assert item.source == site_id
        assert item.details.url.startswith("https://")
        assert item.details.title


@pytest.mark.parametrize("site_id", sorted(EXPECTED_DETAIL))
async def test_detail_page_enrichment(site_id, monkeypatch):
    items, _ = await crawl_fixture(site_id, monkeypatch)
    first, want = items[0], EXPECTED_DETAIL[site_id]

    assert first.published_at == want["published_at"]
    assert first.advertiser is not None and first.advertiser.phone == want["phone"]
    assert len(first.details.media_list or []) >= want["media_min"]
    assert (first.details.city_name, first.details.district_name) == (
        want["city"],
        want["district"],
    )


async def test_off_domain_detail_links_are_never_fetched():
    import asyncio
    from unittest.mock import AsyncMock

    from realestate.infrastructure.config.site_configs import load_site_config
    from realestate.infrastructure.crawler.spiders.generic_html_spider import GenericHtmlSpider
    from tests.conftest import SITES_DIR

    spider = GenericHtmlSpider(load_site_config(SITES_DIR / "mogi.yaml"))
    spider._send_request = AsyncMock()
    item = {"url": "https://ads.example.net/landing"}
    await spider._enrich_from_detail(item, asyncio.Semaphore(1))
    spider._send_request.assert_not_awaited()
    assert item == {"url": "https://ads.example.net/landing"}
