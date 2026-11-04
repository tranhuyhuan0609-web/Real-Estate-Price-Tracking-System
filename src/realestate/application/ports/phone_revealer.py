from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, Tuple


class PhoneRevealerProtocol(Protocol):
    """Protocol for revealing unmasked phone numbers from listings."""

    async def reveal_phone(self, url: str, source: str) -> Optional[str]: ...

    async def reveal_email(self, url: str, source: str) -> Optional[str]: ...

    async def reveal_contact(
        self, url: str, source: str
    ) -> Tuple[Optional[str], Optional[str]]: ...


class PhoneListingRepository(Protocol):
    """Protocol for fetching and persisting revealed advertiser phone numbers."""

    async def get_listing_for_phone_reveal(self, listing_key: str) -> Optional[Dict[str, Any]]: ...

    async def update_advertiser_phone(
        self,
        listing_key: str,
        source: str,
        advertiser_id: Optional[str],
        advertiser_name: Optional[str],
        phone: str,
    ) -> str: ...

    async def update_advertiser_contact(
        self,
        listing_key: str,
        source: str,
        advertiser_id: Optional[str],
        advertiser_name: Optional[str],
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> str: ...
