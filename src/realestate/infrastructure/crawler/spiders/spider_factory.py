"""Spider Factory instantiating either GenericApiSpider or GenericHtmlSpider based on crawler_type."""

from __future__ import annotations

from realestate.application.ports.spider import Spider
from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.infrastructure.crawler.spiders.generic_api_spider import GenericApiSpider
from realestate.infrastructure.crawler.spiders.generic_html_spider import GenericHtmlSpider


def create_spider(config: CrawlerConfig) -> Spider:
    """Factory creating the appropriate Spider implementation based on config.crawler_type."""
    if config.crawler_type == "html":
        return GenericHtmlSpider(config)
    return GenericApiSpider(config)
