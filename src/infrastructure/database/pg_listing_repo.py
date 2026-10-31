"""PostgreSQL repository for Silver (Cleaned Listings & Advertisers) and Gold (Price History) layers."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, List, Optional, Tuple
from psycopg.rows import dict_row
from src.application.interfaces.listing_repository import ListingRepository
from src.domain.entities import (
    AdvertiserData,
    ParsedListingItem,
    PriceStatus,
)
from src.domain.services import evaluate_price_change

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class PgListingRepository(ListingRepository):
    """Manages transactional ingestion of Silver & Gold data models."""

    def __init__(self, pool: "AsyncConnectionPool") -> None:
        self.pool = pool

    async def upsert_advertiser(self, cur, adv: Optional[AdvertiserData]) -> Optional[str]:
        """Step 1 (Silver - Advertiser): Upsert advertiser and return advertiser_id."""
        if not adv or not adv.advertiser_id:
            return None

        query = """
            INSERT INTO advertisers (advertiser_id, phone, name, email, kind, is_verified, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (advertiser_id) DO UPDATE SET
                phone = COALESCE(EXCLUDED.phone, advertisers.phone),
                name = COALESCE(EXCLUDED.name, advertisers.name),
                email = COALESCE(EXCLUDED.email, advertisers.email),
                kind = CASE WHEN EXCLUDED.kind != 'unknown' THEN EXCLUDED.kind ELSE advertisers.kind END,
                is_verified = advertisers.is_verified OR EXCLUDED.is_verified,
                updated_at = NOW();
        """
        await cur.execute(
            query,
            (adv.advertiser_id, adv.phone, adv.name, adv.email, adv.kind.value, adv.is_verified),
        )
        return adv.advertiser_id

    async def _ingest_item_with_cursor(
        self,
        cur,
        item: ParsedListingItem,
        crawl_run_id: str,
    ) -> Tuple[bool, bool]:
        """Internal worker executing upserts for advertiser, listing, details and price history."""
        # Step 1: Upsert Advertiser
        advertiser_id = await self.upsert_advertiser(cur, item.advertiser)

        # Step 2: Lock & query current listing
        await cur.execute(
            "SELECT current_price_vnd, current_price_per_m2, status FROM listings WHERE listing_key = %s FOR UPDATE;",
            (item.listing_key,),
        )
        existing = await cur.fetchone()
        is_new = existing is None
        price_changed = False
        if is_new:
            # Insert new Listing (Silver)
            initial_status = "contact" if (item.current_price_vnd is None or item.current_price_vnd <= 0) else "initial"
            insert_listing_query = """
                INSERT INTO listings (
                    listing_key, property_fingerprint, source, external_id,
                    advertiser_id, status, missing_count, first_seen_at, last_seen_at, published_at,
                    category, property_type, area_m2, facade_m, depth_m,
                    current_price_vnd, current_price_per_m2, price_status, price_changed_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, 'active', 0, NOW(), NOW(), %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, NOW()
                );
            """
            await cur.execute(
                insert_listing_query,
                (
                    item.listing_key, item.property_fingerprint, item.source, item.external_id,
                    advertiser_id, item.published_at,
                    item.category.value, item.property_type.value, item.area_m2, item.facade_m, item.depth_m,
                    item.current_price_vnd, item.current_price_per_m2, initial_status,
                ),
            )
            # Insert Listing Details (Silver)
            d = item.details
            media_json = json.dumps(d.media_list) if d.media_list is not None else None
            geom_point = f"SRID=4326;POINT({d.lng} {d.lat})" if d.lng is not None and d.lat is not None else None
            insert_details_query = """
                INSERT INTO listing_details (
                    listing_key, url, title, description,
                    city_name, district_name, ward_name,
                    lng, lat, geom, legal_status, thumbnail_url, media_list
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, ST_GeomFromEWKT(%s), %s, %s, %s
                );
            """
            await cur.execute(
                insert_details_query,
                (
                    item.listing_key, d.url, d.title, d.description,
                    d.city_name, d.district_name, d.ward_name,
                    d.lng, d.lat, geom_point, d.legal_status, d.thumbnail_url, media_json,
                ),
            )
            # Insert initial price history (Gold) if valid price
            if item.current_price_vnd is not None and item.current_price_vnd > 0:
                insert_history_query = """
                    INSERT INTO listing_price_history (
                        listing_key, crawl_run_id, price_vnd, price_per_m2,
                        price_diff_vnd, price_diff_percent, price_status, recorded_at
                    ) VALUES (%s, %s, %s, %s, 0, 0, 'initial', NOW());
                """
                await cur.execute(
                    insert_history_query,
                    (item.listing_key, crawl_run_id, item.current_price_vnd, item.current_price_per_m2),
                )
        else:
            # Existing listing: check price evolution
            old_price = existing["current_price_vnd"]
            eval_res = evaluate_price_change(old_price, item.current_price_vnd)

            update_price = False
            new_price_vnd = existing.get("current_price_vnd")
            new_price_m2 = existing.get("current_price_per_m2")
            new_price_status = existing.get("price_status")

            if eval_res.price_status in (PriceStatus.INCREASED, PriceStatus.DECREASED, PriceStatus.INITIAL) and item.current_price_vnd and item.current_price_vnd > 0:
                price_changed = True
                update_price = True
                new_price_vnd = item.current_price_vnd
                new_price_m2 = item.current_price_per_m2
                new_price_status = eval_res.price_status.value
            elif eval_res.price_status == PriceStatus.CONTACT and old_price is not None:
                price_changed = True
                update_price = True
                new_price_vnd = None
                new_price_m2 = None
                new_price_status = "contact"

            # Unified single UPDATE statement for listings
            update_listing_query = """
                UPDATE listings SET
                    last_seen_at = NOW(),
                    ended_at = NULL,
                    missing_count = 0,
                    status = 'active',
                    advertiser_id = COALESCE(%s, advertiser_id),
                    published_at = COALESCE(%s, published_at),
                    property_fingerprint = %s,
                    property_type = %s,
                    category = %s,
                    area_m2 = COALESCE(%s, area_m2),
                    facade_m = COALESCE(%s, facade_m),
                    depth_m = COALESCE(%s, depth_m),
                    current_price_vnd = CASE WHEN %s THEN %s ELSE current_price_vnd END,
                    current_price_per_m2 = CASE WHEN %s THEN %s ELSE current_price_per_m2 END,
                    price_status = CASE WHEN %s THEN %s ELSE price_status END,
                    price_changed_at = CASE WHEN %s THEN NOW() ELSE price_changed_at END
                WHERE listing_key = %s;
            """
            await cur.execute(
                update_listing_query,
                (
                    advertiser_id,
                    item.published_at,
                    item.property_fingerprint,
                    item.property_type.value,
                    item.category.value,
                    item.area_m2,
                    item.facade_m,
                    item.depth_m,
                    update_price, new_price_vnd,
                    update_price, new_price_m2,
                    update_price, new_price_status,
                    update_price,
                    item.listing_key,
                ),
            )

            if price_changed and item.current_price_vnd and item.current_price_vnd > 0:
                # Record in listing_price_history (Gold)
                insert_history_query = """
                    INSERT INTO listing_price_history (
                        listing_key, crawl_run_id, price_vnd, price_per_m2,
                        price_diff_vnd, price_diff_percent, price_status, recorded_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW());
                """
                await cur.execute(
                    insert_history_query,
                    (
                        item.listing_key,
                        crawl_run_id,
                        item.current_price_vnd,
                        item.current_price_per_m2,
                        eval_res.price_diff_vnd,
                        eval_res.price_diff_percent,
                        eval_res.price_status.value,
                    ),
                )
            # 2.1 & 3.1: Update listing_details only when distinct to eliminate MVCC bloat
            d = item.details
            media_json = json.dumps(d.media_list) if d.media_list is not None else None
            geom_point = f"SRID=4326;POINT({d.lng} {d.lat})" if d.lng is not None and d.lat is not None else None
            update_details_query = """
                UPDATE listing_details SET
                    url = %s,
                    title = %s,
                    description = COALESCE(%s, description),
                    city_name = COALESCE(%s, city_name),
                    district_name = COALESCE(%s, district_name),
                    ward_name = COALESCE(%s, ward_name),
                    lng = COALESCE(%s, lng),
                    lat = COALESCE(%s, lat),
                    geom = COALESCE(ST_GeomFromEWKT(%s), geom),
                    legal_status = COALESCE(%s, legal_status),
                    thumbnail_url = COALESCE(%s, thumbnail_url),
                    media_list = COALESCE(%s, media_list),
                    updated_at = NOW()
                WHERE listing_key = %s
                  AND (
                      title, description, city_name, district_name, ward_name,
                      lng, lat, legal_status, thumbnail_url, url
                  ) IS DISTINCT FROM (
                      %s, COALESCE(%s, description), COALESCE(%s, city_name), COALESCE(%s, district_name), COALESCE(%s, ward_name),
                      COALESCE(%s, lng), COALESCE(%s, lat), COALESCE(%s, legal_status), COALESCE(%s, thumbnail_url), %s
                  );
            """
            await cur.execute(
                update_details_query,
                (
                    d.url, d.title, d.description,
                    d.city_name, d.district_name, d.ward_name,
                    d.lng, d.lat, geom_point, d.legal_status, d.thumbnail_url, media_json,
                    item.listing_key,
                    d.title, d.description, d.city_name, d.district_name, d.ward_name,
                    d.lng, d.lat, d.legal_status, d.thumbnail_url, d.url,
                ),
            )
        return (is_new, price_changed)

    async def ingest_listing_transactional(
        self,
        item: ParsedListingItem,
        crawl_run_id: str,
        update_crawl_run: bool = True,
    ) -> Tuple[bool, bool]:
        """Execute atomic Silver & Gold ingestion within a transaction.

        To prevent lock contention on crawl_runs when multiple consumer workers run
        concurrently, updating crawl_runs metrics is decoupled from the listing's transaction.

        Returns:
            Tuple[bool, bool]: (is_new_listing, price_changed)
        """
        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    is_new, price_changed = await self._ingest_item_with_cursor(cur, item, crawl_run_id)

            # Step 3 (Decoupled): Update crawl_runs metrics OUTSIDE listing transaction
            if update_crawl_run and crawl_run_id:
                async with conn.cursor() as cur:
                    update_run_query = """
                        UPDATE crawl_runs SET
                            items_processed = items_processed + 1,
                            new_listings_count = new_listings_count + %s,
                            price_changes_detected = price_changes_detected + %s
                        WHERE crawl_run_id = %s;
                    """
                    await cur.execute(
                        update_run_query,
                        (1 if is_new else 0, 1 if price_changed else 0, crawl_run_id),
                    )

            return (is_new, price_changed)

    async def ingest_batch_transactional(
        self,
        batch: List[Tuple[ParsedListingItem, str]],
    ) -> List[Tuple[bool, bool]]:
        """Execute atomic Silver & Gold batch ingestion within a single transaction.

        Sorts items by listing_key before taking row locks to guarantee deterministic lock
        ordering across concurrent worker transactions, completely eliminating deadlock risks.

        Returns:
            List[Tuple[bool, bool]]: list of (is_new, price_changed) for each item in the batch,
            preserving the caller's original batch order.
        """
        if not batch:
            return []

        # Index items and sort by listing_key to enforce global deterministic lock order
        indexed_batch = list(enumerate(batch))
        indexed_batch.sort(key=lambda x: x[1][0].listing_key or f"{x[1][0].source}_{x[1][0].external_id}")

        results_by_index: List[Optional[Tuple[bool, bool]]] = [None] * len(batch)
        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    for orig_idx, (item, crawl_run_id) in indexed_batch:
                        res = await self._ingest_item_with_cursor(cur, item, crawl_run_id)
                        results_by_index[orig_idx] = res

        return [r for r in results_by_index if r is not None]

    async def batch_update_crawl_run_metrics(
        self,
        crawl_run_id: str,
        *,
        items_processed: int,
        new_listings_count: int = 0,
        price_changes_detected: int = 0,
    ) -> None:
        """Batch update crawl_runs metrics from consumer worker."""
        if not crawl_run_id or items_processed <= 0:
            return
        query = """
            UPDATE crawl_runs SET
                items_processed = items_processed + %s,
                new_listings_count = new_listings_count + %s,
                price_changes_detected = price_changes_detected + %s
            WHERE crawl_run_id = %s;
        """
        async with self.pool.connection() as conn:
            await conn.execute(
                query,
                (items_processed, new_listings_count, price_changes_detected, crawl_run_id),
            )

