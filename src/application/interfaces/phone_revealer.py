"""Interfaces for phone reveal use cases."""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol


class PhoneRevealerProtocol(Protocol):
    """Protocol for revealing unmasked phone numbers from listings."""

    async def reveal_phone(self, url: str, source: str) -> Optional[str]:
        """Reveal unmasked phone number from page or detail API."""
        ...


class PhoneListingRepositoryProtocol(Protocol):
    """Protocol for fetching and persisting revealed advertiser phone numbers."""

    async def get_listing_for_phone_reveal(self, listing_key: str) -> Optional[Dict[str, Any]]:
        """Fetch listing details and linked advertiser info required for phone reveal."""
        ...

    async def update_advertiser_phone(
        self,
        listing_key: str,
        source: str,
        advertiser_id: Optional[str],
        advertiser_name: Optional[str],
        phone: str,
    ) -> str:
        """Persist revealed phone to advertiser and ensure listing is linked."""
        ...
