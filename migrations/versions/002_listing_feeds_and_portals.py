"""Listing feeds and portal-scoped listing keys (ADR 0005).

* ``listing_feeds`` records which crawler feed (``crawler_configs.site_id``) has seen a listing,
  so lifecycle can be applied per feed while several feeds share one listing.
* ``listings.source`` now holds the *portal*, which is not necessarily a crawler config, so its
  foreign key to ``crawler_configs`` is dropped.
* Listings of feeds that share a portal's ID space are re-keyed onto the portal
  (``mogi_apartment_123`` -> ``mogi_123``). When both keys already exist they are merged into the
  portal row. ``FEEDS_TO_PORTAL`` is a snapshot of ``configs/sites/*.yaml`` at the time of writing.

Revision ID: 002_listing_feeds
Revises: 001_initial
Create Date: 2026-11-05 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "002_listing_feeds"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FEEDS_TO_PORTAL = (
    ("homedy_apartment", "homedy"),
    ("mogi_apartment", "mogi"),
    ("nhatot_hn", "nhatot"),
)


def _rekey_sql(feed: str, portal: str) -> str:
    old_prefix, new_prefix = f"{feed}_", f"{portal}_"
    # Keys only ever contain [a-z0-9_-] (see build_listing_key), so inlining them is safe.
    pairs = f"""
        SELECT o.listing_key AS old_key, '{new_prefix}' || substr(o.listing_key, {len(old_prefix) + 1}) AS new_key
        FROM listings o
        WHERE left(o.listing_key, {len(old_prefix)}) = '{old_prefix}'
    """
    return f"""
    -- {feed} -> {portal}: merge rows whose portal key already exists ...
    INSERT INTO listing_feeds (listing_key, site_id, first_seen_at, last_seen_at)
    SELECT pr.new_key, f.site_id, f.first_seen_at, f.last_seen_at
    FROM ({pairs}) pr
    JOIN listings p ON p.listing_key = pr.new_key
    JOIN listing_feeds f ON f.listing_key = pr.old_key
    ON CONFLICT (listing_key, site_id) DO UPDATE SET
        first_seen_at = LEAST(listing_feeds.first_seen_at, EXCLUDED.first_seen_at),
        last_seen_at = GREATEST(listing_feeds.last_seen_at, EXCLUDED.last_seen_at);

    UPDATE listings p SET
        first_seen_at = LEAST(p.first_seen_at, o.first_seen_at),
        last_seen_at = GREATEST(p.last_seen_at, o.last_seen_at),
        status = CASE WHEN o.last_seen_at > p.last_seen_at THEN o.status ELSE p.status END,
        missing_count = CASE WHEN o.last_seen_at > p.last_seen_at THEN o.missing_count ELSE p.missing_count END,
        ended_at = CASE WHEN o.last_seen_at > p.last_seen_at THEN o.ended_at ELSE p.ended_at END
    FROM ({pairs}) pr
    JOIN listings o ON o.listing_key = pr.old_key
    WHERE p.listing_key = pr.new_key;

    DELETE FROM listings o
    USING ({pairs}) pr
    JOIN listings p ON p.listing_key = pr.new_key
    WHERE o.listing_key = pr.old_key;

    -- ... then move the remaining rows (ON UPDATE CASCADE carries details, history and feeds).
    UPDATE listings
    SET listing_key = '{new_prefix}' || substr(listing_key, {len(old_prefix) + 1}),
        source = '{portal}'
    WHERE left(listing_key, {len(old_prefix)}) = '{old_prefix}';
    """


def upgrade() -> None:
    op.execute("ALTER TABLE listings DROP CONSTRAINT IF EXISTS listings_source_fkey;")
    op.execute("""
    ALTER TABLE listing_details
        DROP CONSTRAINT IF EXISTS listing_details_listing_key_fkey,
        ADD CONSTRAINT listing_details_listing_key_fkey FOREIGN KEY (listing_key)
            REFERENCES listings(listing_key) ON DELETE CASCADE ON UPDATE CASCADE;
    ALTER TABLE listing_price_history
        DROP CONSTRAINT IF EXISTS listing_price_history_listing_key_fkey,
        ADD CONSTRAINT listing_price_history_listing_key_fkey FOREIGN KEY (listing_key)
            REFERENCES listings(listing_key) ON DELETE CASCADE ON UPDATE CASCADE;
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS listing_feeds (
        listing_key TEXT NOT NULL REFERENCES listings(listing_key) ON DELETE CASCADE ON UPDATE CASCADE,
        site_id VARCHAR(50) NOT NULL REFERENCES crawler_configs(site_id) ON DELETE CASCADE,
        first_seen_at TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (listing_key, site_id),
        CONSTRAINT chk_feed_time_order CHECK (last_seen_at >= first_seen_at)
    );
    CREATE INDEX IF NOT EXISTS idx_listing_feeds_site ON listing_feeds(site_id, listing_key);
    """)
    # Until now every listing belonged to exactly the feed it was keyed by.
    op.execute("""
    INSERT INTO listing_feeds (listing_key, site_id, first_seen_at, last_seen_at)
    SELECT listing_key, source, first_seen_at, last_seen_at FROM listings
    ON CONFLICT DO NOTHING;
    """)
    for feed, portal in FEEDS_TO_PORTAL:
        op.execute(_rekey_sql(feed, portal))
    op.execute("REFRESH MATERIALIZED VIEW mv_ward_avg_price_per_m2;")
    op.execute("REFRESH MATERIALIZED VIEW mv_steep_price_drops_7d;")


def downgrade() -> None:
    # Re-keyed listings keep their portal keys: splitting merged rows back is not possible.
    op.execute("DROP TABLE IF EXISTS listing_feeds;")
    op.execute("""
    ALTER TABLE listing_details
        DROP CONSTRAINT IF EXISTS listing_details_listing_key_fkey,
        ADD CONSTRAINT listing_details_listing_key_fkey FOREIGN KEY (listing_key)
            REFERENCES listings(listing_key) ON DELETE CASCADE;
    ALTER TABLE listing_price_history
        DROP CONSTRAINT IF EXISTS listing_price_history_listing_key_fkey,
        ADD CONSTRAINT listing_price_history_listing_key_fkey FOREIGN KEY (listing_key)
            REFERENCES listings(listing_key) ON DELETE CASCADE;
    """)
