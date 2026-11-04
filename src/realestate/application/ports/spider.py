from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Protocol, Tuple

from realestate.domain.entities.crawl_run import RunMode
from realestate.domain.entities.listing import ParsedListingItem


class Spider(Protocol):
    """Protocol for site spiders."""

    pages_crawled: int

    def crawl(
        self,
        run_mode: RunMode = RunMode.INCREMENTAL,
    ) -> AsyncIterator[Tuple[ParsedListingItem, Dict[str, Any]]]:
        """Yield (parsed item, raw payload) pairs. Implementations are ``async def`` generators."""
        ...

