from __future__ import annotations
from typing import Any, AsyncGenerator, Dict, Protocol, Tuple
from realestate.domain.entities.listing import ParsedListingItem
from realestate.domain.entities.crawl_run import RunMode


class Spider(Protocol):
    """Protocol for site spiders."""

    pages_crawled: int

    async def crawl(
        self,
        run_mode: RunMode = RunMode.INCREMENTAL,
    ) -> AsyncGenerator[Tuple[ParsedListingItem, Dict[str, Any]], None]:
        ...

