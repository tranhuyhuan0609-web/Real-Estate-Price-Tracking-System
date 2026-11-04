"""001_initial_schema

Revision ID: 001_initial
Revises:
Create Date: 2026-11-03 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")

    # 1. crawler_configs
    op.execute("""
    CREATE TABLE IF NOT EXISTS crawler_configs (
        site_id VARCHAR(50) PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT true,
        crawler_type TEXT NOT NULL DEFAULT 'rest_api',
        schedule_times TEXT[] NOT NULL DEFAULT '{}',
        interval_seconds INT DEFAULT 300,
        config_json JSONB NOT NULL,
        last_run_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT chk_crawler_type CHECK (crawler_type IN ('rest_api', 'graphql', 'html')),
        CONSTRAINT chk_interval_positive CHECK (interval_seconds > 0),
        CONSTRAINT chk_config_is_object CHECK (jsonb_typeof(config_json) = 'object')
    );
    """)

    # 2. crawl_runs
    op.execute("""
    CREATE TABLE IF NOT EXISTS crawl_runs (
        crawl_run_id TEXT PRIMARY KEY,
        site_id VARCHAR(50) NOT NULL REFERENCES crawler_configs(site_id) ON DELETE CASCADE,
        run_mode VARCHAR(20) NOT NULL DEFAULT 'incremental',
        status VARCHAR(20) NOT NULL DEFAULT 'running',
        pages_crawled INT NOT NULL DEFAULT 0,
        items_scraped INT NOT NULL DEFAULT 0,
        items_processed INT NOT NULL DEFAULT 0,
        new_listings_count INT NOT NULL DEFAULT 0,
        price_changes_detected INT NOT NULL DEFAULT 0,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at TIMESTAMPTZ,
        lifecycle_applied_at TIMESTAMPTZ,
        error_message TEXT,
        CONSTRAINT chk_run_mode CHECK (run_mode IN ('incremental', 'full_sweep')),
        CONSTRAINT chk_status CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
        CONSTRAINT chk_time_order CHECK (finished_at IS NULL OR finished_at >= started_at)
    );
    CREATE INDEX IF NOT EXISTS idx_crawl_runs_site_history ON crawl_runs(site_id, started_at DESC);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_crawl_runs_one_running_per_site ON crawl_runs(site_id) WHERE status = 'running';
    """)

    # 3. advertisers
    op.execute("""
    CREATE TABLE IF NOT EXISTS advertisers (
        advertiser_id VARCHAR(64) PRIMARY KEY,
        phone VARCHAR(20),
        name VARCHAR(100),
        email VARCHAR(100),
        kind VARCHAR(15) NOT NULL DEFAULT 'unknown',
        is_verified BOOLEAN NOT NULL DEFAULT false,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT chk_advertiser_kind CHECK (kind IN ('broker', 'owner', 'unknown'))
    );
    CREATE INDEX IF NOT EXISTS idx_advertisers_phone ON advertisers(phone) WHERE phone IS NOT NULL;
    """)

    # 4. listings
    op.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        listing_key TEXT PRIMARY KEY,
        property_fingerprint TEXT,
        source VARCHAR(50) NOT NULL REFERENCES crawler_configs(site_id),
        external_id TEXT,
        advertiser_id VARCHAR(64) REFERENCES advertisers(advertiser_id) ON DELETE SET NULL,
        status TEXT NOT NULL DEFAULT 'active',
        missing_count INT NOT NULL DEFAULT 0,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        published_at TIMESTAMPTZ,
        ended_at TIMESTAMPTZ,
        category VARCHAR(20) NOT NULL,
        property_type VARCHAR(20) NOT NULL DEFAULT 'land',
        area_m2 NUMERIC(10, 2),
        facade_m NUMERIC(6, 2),
        depth_m NUMERIC(6, 2),
        current_price_vnd NUMERIC(18, 0),
        current_price_per_m2 NUMERIC(14, 0),
        price_status VARCHAR(20) NOT NULL DEFAULT 'initial',
        price_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT chk_listing_status CHECK (status IN ('active', 'expired', 'hidden')),
        CONSTRAINT chk_category CHECK (category IN ('sale', 'rent', 'project')),
        CONSTRAINT chk_property_type CHECK (property_type IN ('land', 'house', 'apartment', 'villa', 'shophouse', 'commercial', 'other')),
        CONSTRAINT chk_price_status CHECK (price_status IN ('initial', 'increased', 'decreased', 'contact')),
        CONSTRAINT chk_missing_count_non_negative CHECK (missing_count >= 0),
        CONSTRAINT chk_positive_dimensions CHECK (
            (area_m2 IS NULL OR area_m2 > 0) AND
            (facade_m IS NULL OR facade_m > 0) AND
            (depth_m IS NULL OR depth_m > 0)
        ),
        CONSTRAINT chk_non_negative_price CHECK (
            (current_price_vnd IS NULL OR current_price_vnd >= 0) AND
            (current_price_per_m2 IS NULL OR current_price_per_m2 >= 0)
        ),
        CONSTRAINT chk_listing_time_order CHECK (last_seen_at >= first_seen_at)
    );
    CREATE INDEX IF NOT EXISTS idx_listings_active_filter ON listings(category, property_type, current_price_per_m2) WHERE status = 'active';
    CREATE INDEX IF NOT EXISTS idx_listings_price_changed ON listings(price_changed_at DESC);
    CREATE INDEX IF NOT EXISTS idx_listings_source_last_seen ON listings(source, last_seen_at) WHERE status = 'active';
    CREATE INDEX IF NOT EXISTS idx_listings_advertiser ON listings(advertiser_id);
    CREATE INDEX IF NOT EXISTS idx_listings_fingerprint ON listings(property_fingerprint);
    """)

    # 5. listing_details
    op.execute("""
    CREATE TABLE IF NOT EXISTS listing_details (
        listing_key TEXT PRIMARY KEY REFERENCES listings(listing_key) ON DELETE CASCADE,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        city_name TEXT,
        district_name TEXT,
        ward_name TEXT,
        lng DOUBLE PRECISION,
        lat DOUBLE PRECISION,
        geom GEOMETRY(Point, 4326),
        legal_status VARCHAR(50),
        thumbnail_url TEXT,
        media_list JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_listing_details_geom ON listing_details USING GIST(geom);
    CREATE INDEX IF NOT EXISTS idx_listing_details_location ON listing_details(city_name, district_name, ward_name);
    """)

    # 6. listing_price_history
    op.execute("""
    CREATE TABLE IF NOT EXISTS listing_price_history (
        history_id BIGSERIAL PRIMARY KEY,
        listing_key TEXT NOT NULL REFERENCES listings(listing_key) ON DELETE CASCADE,
        crawl_run_id TEXT REFERENCES crawl_runs(crawl_run_id) ON DELETE SET NULL,
        price_vnd NUMERIC(18, 0) NOT NULL,
        price_per_m2 NUMERIC(14, 0),
        price_diff_vnd NUMERIC(18, 0) NOT NULL DEFAULT 0,
        price_diff_percent NUMERIC(12, 2) NOT NULL DEFAULT 0,
        price_status VARCHAR(20) NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT chk_history_price_positive CHECK (price_vnd > 0 AND (price_per_m2 IS NULL OR price_per_m2 > 0)),
        CONSTRAINT chk_history_status CHECK (price_status IN ('initial', 'increased', 'decreased')),
        CONSTRAINT chk_price_diff_logic CHECK (
            (price_status = 'initial' AND price_diff_vnd = 0) OR
            (price_status = 'increased' AND price_diff_vnd > 0) OR
            (price_status = 'decreased' AND price_diff_vnd < 0)
        )
    );
    CREATE INDEX IF NOT EXISTS idx_price_history_listing_timeline ON listing_price_history(listing_key, recorded_at DESC);
    CREATE INDEX IF NOT EXISTS idx_price_history_recorded_at ON listing_price_history(recorded_at DESC);
    """)

    # 7. Views
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS mv_ward_avg_price_per_m2 AS
    SELECT
        d.city_name,
        d.district_name,
        COALESCE(d.ward_name, '') AS ward_name,
        l.category,
        l.property_type,
        COUNT(l.listing_key) AS total_active_listings,
        ROUND(AVG(l.current_price_per_m2), 0) AS avg_price_per_m2_vnd,
        ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY l.current_price_per_m2)::NUMERIC, 0) AS median_price_per_m2_vnd,
        ROUND(MIN(l.current_price_per_m2), 0) AS min_price_per_m2_vnd,
        ROUND(MAX(l.current_price_per_m2), 0) AS max_price_per_m2_vnd,
        NOW() AS refreshed_at
    FROM listings l
    JOIN listing_details d ON l.listing_key = d.listing_key
    WHERE l.status = 'active'
      AND l.current_price_per_m2 BETWEEN 1000000 AND 2000000000
      AND d.city_name IS NOT NULL
    GROUP BY
        d.city_name,
        d.district_name,
        COALESCE(d.ward_name, ''),
        l.category,
        l.property_type;

    CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_ward_avg_price
    ON mv_ward_avg_price_per_m2(city_name, district_name, ward_name, category, property_type);

    CREATE MATERIALIZED VIEW IF NOT EXISTS mv_steep_price_drops_7d AS
    SELECT
        h.history_id,
        h.listing_key,
        l.source,
        d.title,
        d.url,
        d.city_name,
        d.district_name,
        d.ward_name,
        l.area_m2,
        h.price_vnd AS new_price_vnd,
        (h.price_vnd - h.price_diff_vnd) AS old_price_vnd,
        h.price_diff_vnd,
        h.price_diff_percent,
        h.recorded_at,
        NOW() AS refreshed_at
    FROM listing_price_history h
    JOIN listings l ON h.listing_key = l.listing_key
    JOIN listing_details d ON h.listing_key = d.listing_key
    WHERE h.price_status = 'decreased'
      AND h.price_diff_percent <= -5.0
      AND h.recorded_at >= NOW() - INTERVAL '7 days'
    ORDER BY h.price_diff_percent ASC;

    CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_steep_drops_history ON mv_steep_price_drops_7d(history_id);
    CREATE INDEX IF NOT EXISTS idx_mv_steep_drops_listing ON mv_steep_price_drops_7d(listing_key);
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_steep_price_drops_7d CASCADE;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_ward_avg_price_per_m2 CASCADE;")
    op.execute("DROP TABLE IF EXISTS listing_price_history CASCADE;")
    op.execute("DROP TABLE IF EXISTS listing_details CASCADE;")
    op.execute("DROP TABLE IF EXISTS listings CASCADE;")
    op.execute("DROP TABLE IF EXISTS advertisers CASCADE;")
    op.execute("DROP TABLE IF EXISTS crawl_runs CASCADE;")
    op.execute("DROP TABLE IF EXISTS crawler_configs CASCADE;")
