from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Protocol, Tuple

from realestate.domain.entities.listing import ParsedListingItem


@dataclass(frozen=True)
class ListingObservation:
    """One parsed listing as seen by one crawl run at ``event_time`` (None = now)."""

    item: ParsedListingItem
    crawl_run_id: str
    event_time: Optional[datetime] = None
    feed: Optional[str] = None  # crawler config that saw it; defaults to item.source (portal)


class ListingRepository(Protocol):
    """Protocol for managing listings, advertisers, and price history."""

    async def ingest_listing_transactional(
        self,
        item: ParsedListingItem,
        crawl_run_id: str,
        update_crawl_run: bool = True,
        event_time: Optional[datetime] = None,
        feed: Optional[str] = None,
    ) -> Tuple[bool, bool]:
        """Ingest a parsed listing into relational silver & gold layers."""
        ...

    async def ingest_batch_transactional(
        self,
        batch: Sequence[ListingObservation],
    ) -> List[Tuple[bool, bool]]:
        """Ingest a batch atomically; returns (is_new, price_changed) per observation, in input order."""
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
