"""PostgreSQL implementation of PhoneListingRepository."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from psycopg.rows import dict_row

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class PgPhoneRepository:
    pass

class PgPhoneListingRepository(PgPhoneRepository):
    """Handles database queries and updates for phone reveal operations."""

    def __init__(self, pool: "AsyncConnectionPool") -> None:
        self.pool = pool

    async def get_listing_for_phone_reveal(self, listing_key: str) -> Optional[Dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT
                        l.listing_key,
                        l.source,
                        l.advertiser_id,
                        ld.url,
                        a.phone AS advertiser_phone,
                        a.email AS advertiser_email,
                        a.name AS advertiser_name
                    FROM listings l
                    LEFT JOIN listing_details ld ON l.listing_key = ld.listing_key
                    LEFT JOIN advertisers a ON l.advertiser_id = a.advertiser_id
                    WHERE l.listing_key = %s;
                    """,
                    (listing_key,),
                )
                return await cur.fetchone()

    async def update_advertiser_phone(
        self,
        listing_key: str,
        source: str,
        advertiser_id: Optional[str],
        advertiser_name: Optional[str],
        phone: str,
    ) -> str:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if not advertiser_id:
                    advertiser_id = f"adv_{source}_{phone}"
                    await cur.execute(
                        """
                        INSERT INTO advertisers (advertiser_id, phone, name, kind, is_verified)
                        VALUES (%s, %s, %s, 'unknown', FALSE)
                        ON CONFLICT (advertiser_id) DO UPDATE SET
                            phone = EXCLUDED.phone,
                            name = COALESCE(advertisers.name, EXCLUDED.name);
                        """,
                        (advertiser_id, phone, advertiser_name),
                    )
                    await cur.execute(
                        """
                        UPDATE listings SET advertiser_id = %s WHERE listing_key = %s;
                        """,
                        (advertiser_id, listing_key),
                    )
                else:
                    await cur.execute(
                        """
                        UPDATE advertisers SET phone = %s WHERE advertiser_id = %s;
                        """,
                        (phone, advertiser_id),
                    )
                await conn.commit()
                return advertiser_id

    async def update_advertiser_contact(
        self,
        listing_key: str,
        source: str,
        advertiser_id: Optional[str],
        advertiser_name: Optional[str],
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> str:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if not advertiser_id:
                    suffix = phone or (email.split("@")[0] if email else "contact")
                    advertiser_id = f"adv_{source}_{suffix[:20]}"
                    await cur.execute(
                        """
                        INSERT INTO advertisers (advertiser_id, phone, email, name, kind, is_verified)
                        VALUES (%s, %s, %s, %s, 'unknown', FALSE)
                        ON CONFLICT (advertiser_id) DO UPDATE SET
                            phone = COALESCE(EXCLUDED.phone, advertisers.phone),
                            email = COALESCE(EXCLUDED.email, advertisers.email),
                            name = COALESCE(advertisers.name, EXCLUDED.name);
                        """,
                        (advertiser_id, phone, email, advertiser_name),
                    )
                    await cur.execute(
                        """
                        UPDATE listings SET advertiser_id = %s WHERE listing_key = %s;
                        """,
                        (advertiser_id, listing_key),
                    )
                else:
                    await cur.execute(
                        """
                        UPDATE advertisers SET
                            phone = COALESCE(%s, advertisers.phone),
                            email = COALESCE(%s, advertisers.email),
                            name = COALESCE(advertisers.name, %s)
                        WHERE advertiser_id = %s;
                        """,
                        (phone, email, advertiser_name, advertiser_id),
                    )
                await conn.commit()
                return advertiser_id
