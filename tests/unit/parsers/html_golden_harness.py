"""Run GenericHtmlSpider offline against captured pages in tests/fixtures/html/."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
from parsel import Selector

from realestate.domain.entities import RunMode
from realestate.infrastructure.config.site_configs import load_site_config
from realestate.infrastructure.crawler.spiders.generic_html_spider import GenericHtmlSpider
from tests.conftest import FIXTURES_DIR, SITES_DIR

EMPTY_PAGE = "<html><body></body></html>"


async def crawl_fixture(site_id: str, monkeypatch) -> tuple[list, GenericHtmlSpider]:
    """Page 1 = <site>_list.html, page 2 = empty (end of listings); the first card's detail URL
    serves <site>_detail.html and every other detail URL 404s (list data is still kept)."""
    config = load_site_config(SITES_DIR / f"{site_id}.yaml")
    config = config.model_copy(
        update={"rate_limit": config.rate_limit.model_copy(update={"download_delay_sec": 0.0})}
    )
    list_html = (FIXTURES_DIR / "html" / f"{site_id}_list.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES_DIR / "html" / f"{site_id}_detail.html").read_text(encoding="utf-8")
    spider = GenericHtmlSpider(config)

    first_page_url, _ = spider._build_page_url_and_params(config.pagination.start_page, 0)
    cards = Selector(text=list_html).css(config.response_mapping.items_path)
    detail_urls = [
        str(httpx.URL(first_page_url).join(u))
        for u in (
            spider._extract_selector_value(c, config.response_mapping.fields["url"]) for c in cards
        )
        if u
    ]
    list_pages_served = 0

    async def fake_get(url, params=None, timeout=None):
        nonlocal list_pages_served
        url = str(url)
        if url in detail_urls:
            if url == detail_urls[0]:
                return MagicMock(status_code=200, text=detail_html, url=httpx.URL(url))
            return MagicMock(status_code=404, text="", url=httpx.URL(url))
        list_pages_served += 1
        page = list_html if list_pages_served == 1 else EMPTY_PAGE
        return MagicMock(status_code=200, text=page, url=httpx.URL(url))

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)
    client.aclose = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient", MagicMock(return_value=client))

    items = [item async for item, _raw in spider.crawl(RunMode.INCREMENTAL)]
    return items, spider
