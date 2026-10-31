"""Listing repository interface for Silver and Gold persistence."""

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
from src.domain.entities import ParsedListingItem


class ListingRepository(ABC):
    """Abstract interface for managing listings, advertisers, and price history."""

    @abstractmethod
    async def ingest_listing_transactional(
        self,
        item: ParsedListingItem,
        crawl_run_id: str,
        update_crawl_run: bool = True,
    ) -> Tuple[bool, bool]:
        """Ingest a parsed listing into relational silver & gold layers.

        Returns:
            Tuple[bool, bool]: (is_new, price_changed)
        """
        pass

    @abstractmethod
    async def ingest_batch_transactional(
        self,
        batch: List[Tuple[ParsedListingItem, str]],
    ) -> List[Tuple[bool, bool]]:
        """Ingest a batch of parsed listings atomically within a single transaction.

        Returns:
            List[Tuple[bool, bool]]: list of (is_new, price_changed) for each item in batch.
        """
        pass

    @abstractmethod
    async def batch_update_crawl_run_metrics(
        self,
        crawl_run_id: str,
        *,
        items_processed: int,
        new_listings_count: int = 0,
        price_changes_detected: int = 0,
    ) -> None:
        """Batch update crawl run metrics."""
        pass

