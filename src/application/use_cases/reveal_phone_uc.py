"""Use case to reveal and update advertiser phone number for a listing on demand."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

from src.application.interfaces.phone_revealer import (
    PhoneListingRepositoryProtocol,
    PhoneRevealerProtocol,
)

logger = logging.getLogger(__name__)


class RevealPhoneUseCase:
    """Reveals the unmasked phone number for a real estate listing on demand and persists it."""

    def __init__(
        self,
        repo: PhoneListingRepositoryProtocol,
        revealer: PhoneRevealerProtocol,
    ) -> None:
        if repo is None:
            raise ValueError("repo must be provided to RevealPhoneUseCase.")
        if revealer is None:
            raise ValueError("revealer must be provided to RevealPhoneUseCase.")
        self.repo = repo
        self.revealer = revealer

    async def execute(self, listing_key: str) -> Dict[str, Any]:
        """Fetch listing details, reveal unmasked phone number, and update database if found."""
        row = await self.repo.get_listing_for_phone_reveal(listing_key)
        if not row:
            raise ValueError(f"Listing '{listing_key}' not found.")

        url = row.get("url") or ""
        source = row.get("source") or ""
        existing_phone = row.get("advertiser_phone")
        advertiser_id = row.get("advertiser_id")
        advertiser_name = row.get("advertiser_name")

        # If already unmasked valid phone, return early
        if existing_phone and "*" not in existing_phone and len(re.sub(r"\D", "", existing_phone)) >= 9:
            return {
                "listing_key": listing_key,
                "advertiser_phone": existing_phone,
                "status": "already_unmasked",
            }

        # Invoke phone revealer
        revealed_phone = await self.revealer.reveal_phone(url=url, source=source)
        if not revealed_phone:
            return {
                "listing_key": listing_key,
                "advertiser_phone": existing_phone,
                "status": "not_found",
            }

        # Persist revealed phone to database
        advertiser_id = await self.repo.update_advertiser_phone(
            listing_key=listing_key,
            source=source,
            advertiser_id=advertiser_id,
            advertiser_name=advertiser_name,
            phone=revealed_phone,
        )

        logger.info(
            "Phone revealed for listing '%s': %s (advertiser_id: %s)",
            listing_key,
            revealed_phone,
            advertiser_id,
        )
        return {
            "listing_key": listing_key,
            "advertiser_id": advertiser_id,
            "advertiser_phone": revealed_phone,
            "status": "revealed",
        }
