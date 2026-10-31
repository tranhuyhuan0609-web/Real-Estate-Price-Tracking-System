"""Spider Factory instantiating either GenericApiSpider or GenericHtmlSpider based on crawler_type."""

from __future__ import annotations

from src.application.interfaces.spider import Spider
from src.domain.entities.crawler_config import CrawlerConfig
from src.infrastructure.crawler.generic_api_spider import GenericApiSpider
from src.infrastructure.crawler.generic_html_spider import GenericHtmlSpider


def create_spider(config: CrawlerConfig) -> Spider:
    """Factory creating the appropriate Spider implementation based on config.crawler_type."""
    if config.crawler_type == "html":
        return GenericHtmlSpider(config)
    return GenericApiSpider(config)

