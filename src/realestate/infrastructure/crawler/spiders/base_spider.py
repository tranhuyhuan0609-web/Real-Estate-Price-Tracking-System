from __future__ import annotations
from typing import Any, AsyncGenerator, Dict, Optional, Tuple
from realestate.application.ports.spider import Spider
from realestate.domain.entities.listing import ParsedListingItem
from realestate.domain.entities.crawl_run import RunMode


class BaseSpider(Spider):
    """Abstract base class for spiders implementing the Spider protocol."""

    pages_crawled: int = 0
    aborted_reason: Optional[str] = None
    exhausted: bool = False

    async def crawl(
        self,
        run_mode: RunMode = RunMode.INCREMENTAL,
    ) -> AsyncGenerator[Tuple[ParsedListingItem, Dict[str, Any]], None]:
        raise NotImplementedError

