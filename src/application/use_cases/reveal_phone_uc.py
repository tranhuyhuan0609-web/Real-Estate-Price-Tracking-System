"""Use case to reveal and update advertiser phone number for a listing on demand."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, Optional
from psycopg.rows import dict_row

from src.infrastructure.crawler.phone_revealer import PhoneRevealer

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class RevealPhoneUseCase:
    """Reveals the unmasked phone number for a real estate listing on demand and persists it."""

    def __init__(
        self,
        pool: "AsyncConnectionPool",
        revealer: Optional[PhoneRevealer] = None,
    ) -> None:
        self.pool = pool
        self.revealer = revealer or PhoneRevealer()

    async def execute(self, listing_key: str) -> Dict[str, Any]:
        """Fetch listing details, reveal unmasked phone number, and update database if found."""
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 1. Fetch current listing info and linked advertiser
                await cur.execute(
                    """
                    SELECT 
                        l.listing_key,
                        l.source,
                        l.advertiser_id,
                        ld.url,
                        a.phone AS advertiser_phone,
                        a.name AS advertiser_name
                    FROM listings l
                    LEFT JOIN listing_details ld ON l.listing_key = ld.listing_key
                    LEFT JOIN advertisers a ON l.advertiser_id = a.advertiser_id
                    WHERE l.listing_key = %s;
                    """,
                    (listing_key,),
                )
                row = await cur.fetchone()
                if not row:
                    raise ValueError(f"Listing '{listing_key}' not found.")

                url = row["url"] or ""
                source = row["source"] or ""
                existing_phone = row["advertiser_phone"]
                advertiser_id = row["advertiser_id"]
                advertiser_name = row["advertiser_name"]

                # 2. Invoke phone revealer
                revealed_phone = await self.revealer.reveal_phone(
                    url=url,
                    site_id=source,
                    existing_phone=existing_phone,
                )

                if revealed_phone and revealed_phone != existing_phone:
                    # 3. Update or create advertiser record
                    if advertiser_id:
                        await cur.execute(
                            """
                            UPDATE advertisers
                            SET phone = %s, updated_at = NOW()
                            WHERE advertiser_id = %s;
                            """,
                            (revealed_phone, advertiser_id),
                        )
                    else:
                        clean_num = re.sub(r"\D", "", revealed_phone)
                        new_adv_id = f"adv_{source}_{clean_num[:20]}"
                        await cur.execute(
                            """
                            INSERT INTO advertisers (advertiser_id, phone, name, kind, updated_at)
                            VALUES (%s, %s, %s, 'unknown', NOW())
                            ON CONFLICT (advertiser_id) DO UPDATE
                            SET phone = EXCLUDED.phone, updated_at = NOW();
                            """,
                            (new_adv_id, revealed_phone, advertiser_name),
                        )
                        await cur.execute(
                            """
                            UPDATE listings
                            SET advertiser_id = %s
                            WHERE listing_key = %s;
                            """,
                            (new_adv_id, listing_key),
                        )

                    await conn.commit()
                    logger.info("Successfully revealed and saved phone for %s: %s", listing_key, revealed_phone)

                return {
                    "listing_key": listing_key,
                    "url": url,
                    "source": source,
                    "previous_phone": existing_phone,
                    "advertiser_phone": revealed_phone or existing_phone,
                    "status": "revealed" if revealed_phone else "unresolved",
                }
