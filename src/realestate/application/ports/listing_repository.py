from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Protocol, Tuple

from realestate.domain.entities.listing import ParsedListingItem


class ListingRepository(Protocol):
    """Protocol for managing listings, advertisers, and price history."""

    async def ingest_listing_transactional(
        self,
        item: ParsedListingItem,
        crawl_run_id: str,
        update_crawl_run: bool = True,
        event_time: Optional[datetime] = None,
    ) -> Tuple[bool, bool]:
        """Ingest a parsed listing into relational silver & gold layers."""
        ...

    async def ingest_batch_transactional(
        self,
        batch: List[Tuple[Any, ...]],
    ) -> List[Tuple[bool, bool]]:
        """Ingest a batch of parsed listings atomically within a single transaction."""
        ...

    async def batch_update_crawl_run_metrics(
        self,
        crawl_run_id: str,
        *,
        items_processed: int,
        new_listings_count: int = 0,
        price_changes_detected: int = 0,
    ) -> None:
        """Batch update crawl run metrics."""
        ...

