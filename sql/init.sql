-- GENERATED FILE - do not edit. Source of truth: migrations/versions/*.py
-- Regenerate with: make schema  (scripts/generate_init_sql.py)
-- Postgres runs this once when its data volume is empty; later changes ship as migrations.

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 001_initial

CREATE EXTENSION IF NOT EXISTS postgis;;

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
    );;

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
    CREATE UNIQUE INDEX IF NOT EXISTS uq_crawl_runs_one_running_per_site ON crawl_runs(site_id) WHERE status = 'running';;

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
    CREATE INDEX IF NOT EXISTS idx_advertisers_phone ON advertisers(phone) WHERE phone IS NOT NULL;;

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
    CREATE INDEX IF NOT EXISTS idx_listings_fingerprint ON listings(property_fingerprint);;

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
    CREATE INDEX IF NOT EXISTS idx_listing_details_location ON listing_details(city_name, district_name, ward_name);;

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
    CREATE INDEX IF NOT EXISTS idx_price_history_recorded_at ON listing_price_history(recorded_at DESC);;

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
    CREATE INDEX IF NOT EXISTS idx_mv_steep_drops_listing ON mv_steep_price_drops_7d(listing_key);;

INSERT INTO alembic_version (version_num) VALUES ('001_initial') RETURNING alembic_version.version_num;

-- Running upgrade 001_initial -> 002_listing_feeds

ALTER TABLE listings DROP CONSTRAINT IF EXISTS listings_source_fkey;;

ALTER TABLE listing_details
        DROP CONSTRAINT IF EXISTS listing_details_listing_key_fkey,
        ADD CONSTRAINT listing_details_listing_key_fkey FOREIGN KEY (listing_key)
            REFERENCES listings(listing_key) ON DELETE CASCADE ON UPDATE CASCADE;
    ALTER TABLE listing_price_history
        DROP CONSTRAINT IF EXISTS listing_price_history_listing_key_fkey,
        ADD CONSTRAINT listing_price_history_listing_key_fkey FOREIGN KEY (listing_key)
            REFERENCES listings(listing_key) ON DELETE CASCADE ON UPDATE CASCADE;;

CREATE TABLE IF NOT EXISTS listing_feeds (
        listing_key TEXT NOT NULL REFERENCES listings(listing_key) ON DELETE CASCADE ON UPDATE CASCADE,
        site_id VARCHAR(50) NOT NULL REFERENCES crawler_configs(site_id) ON DELETE CASCADE,
        first_seen_at TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (listing_key, site_id),
        CONSTRAINT chk_feed_time_order CHECK (last_seen_at >= first_seen_at)
    );
    CREATE INDEX IF NOT EXISTS idx_listing_feeds_site ON listing_feeds(site_id, listing_key);;

INSERT INTO listing_feeds (listing_key, site_id, first_seen_at, last_seen_at)
    SELECT listing_key, source, first_seen_at, last_seen_at FROM listings
    ON CONFLICT DO NOTHING;;

-- homedy_apartment -> homedy: merge rows whose portal key already exists ...
    INSERT INTO listing_feeds (listing_key, site_id, first_seen_at, last_seen_at)
    SELECT pr.new_key, f.site_id, f.first_seen_at, f.last_seen_at
    FROM (
        SELECT o.listing_key AS old_key, 'homedy_' || substr(o.listing_key, 18) AS new_key
        FROM listings o
        WHERE left(o.listing_key, 17) = 'homedy_apartment_'
    ) pr
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
    FROM (
        SELECT o.listing_key AS old_key, 'homedy_' || substr(o.listing_key, 18) AS new_key
        FROM listings o
        WHERE left(o.listing_key, 17) = 'homedy_apartment_'
    ) pr
    JOIN listings o ON o.listing_key = pr.old_key
    WHERE p.listing_key = pr.new_key;

    DELETE FROM listings o
    USING (
        SELECT o.listing_key AS old_key, 'homedy_' || substr(o.listing_key, 18) AS new_key
        FROM listings o
        WHERE left(o.listing_key, 17) = 'homedy_apartment_'
    ) pr
    JOIN listings p ON p.listing_key = pr.new_key
    WHERE o.listing_key = pr.old_key;

    -- ... then move the remaining rows (ON UPDATE CASCADE carries details, history and feeds).
    UPDATE listings
    SET listing_key = 'homedy_' || substr(listing_key, 18),
        source = 'homedy'
    WHERE left(listing_key, 17) = 'homedy_apartment_';;

-- mogi_apartment -> mogi: merge rows whose portal key already exists ...
    INSERT INTO listing_feeds (listing_key, site_id, first_seen_at, last_seen_at)
    SELECT pr.new_key, f.site_id, f.first_seen_at, f.last_seen_at
    FROM (
        SELECT o.listing_key AS old_key, 'mogi_' || substr(o.listing_key, 16) AS new_key
        FROM listings o
        WHERE left(o.listing_key, 15) = 'mogi_apartment_'
    ) pr
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
    FROM (
        SELECT o.listing_key AS old_key, 'mogi_' || substr(o.listing_key, 16) AS new_key
        FROM listings o
        WHERE left(o.listing_key, 15) = 'mogi_apartment_'
    ) pr
    JOIN listings o ON o.listing_key = pr.old_key
    WHERE p.listing_key = pr.new_key;

    DELETE FROM listings o
    USING (
        SELECT o.listing_key AS old_key, 'mogi_' || substr(o.listing_key, 16) AS new_key
        FROM listings o
        WHERE left(o.listing_key, 15) = 'mogi_apartment_'
    ) pr
    JOIN listings p ON p.listing_key = pr.new_key
    WHERE o.listing_key = pr.old_key;

    -- ... then move the remaining rows (ON UPDATE CASCADE carries details, history and feeds).
    UPDATE listings
    SET listing_key = 'mogi_' || substr(listing_key, 16),
        source = 'mogi'
    WHERE left(listing_key, 15) = 'mogi_apartment_';;

-- nhatot_hn -> nhatot: merge rows whose portal key already exists ...
    INSERT INTO listing_feeds (listing_key, site_id, first_seen_at, last_seen_at)
    SELECT pr.new_key, f.site_id, f.first_seen_at, f.last_seen_at
    FROM (
        SELECT o.listing_key AS old_key, 'nhatot_' || substr(o.listing_key, 11) AS new_key
        FROM listings o
        WHERE left(o.listing_key, 10) = 'nhatot_hn_'
    ) pr
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
    FROM (
        SELECT o.listing_key AS old_key, 'nhatot_' || substr(o.listing_key, 11) AS new_key
        FROM listings o
        WHERE left(o.listing_key, 10) = 'nhatot_hn_'
    ) pr
    JOIN listings o ON o.listing_key = pr.old_key
    WHERE p.listing_key = pr.new_key;

    DELETE FROM listings o
    USING (
        SELECT o.listing_key AS old_key, 'nhatot_' || substr(o.listing_key, 11) AS new_key
        FROM listings o
        WHERE left(o.listing_key, 10) = 'nhatot_hn_'
    ) pr
    JOIN listings p ON p.listing_key = pr.new_key
    WHERE o.listing_key = pr.old_key;

    -- ... then move the remaining rows (ON UPDATE CASCADE carries details, history and feeds).
    UPDATE listings
    SET listing_key = 'nhatot_' || substr(listing_key, 11),
        source = 'nhatot'
    WHERE left(listing_key, 10) = 'nhatot_hn_';;

REFRESH MATERIALIZED VIEW mv_ward_avg_price_per_m2;;

REFRESH MATERIALIZED VIEW mv_steep_price_drops_7d;;

UPDATE alembic_version SET version_num='002_listing_feeds' WHERE alembic_version.version_num = '001_initial';

-- Running upgrade 002_listing_feeds -> 003_partition_history

DROP MATERIALIZED VIEW IF EXISTS mv_steep_price_drops_7d;;

ALTER TABLE listing_price_history RENAME TO listing_price_history_unpartitioned;
    ALTER SEQUENCE listing_price_history_history_id_seq OWNED BY NONE;
    ALTER INDEX IF EXISTS listing_price_history_pkey RENAME TO listing_price_history_unpartitioned_pkey;
    ALTER INDEX IF EXISTS idx_price_history_listing_timeline RENAME TO idx_price_history_listing_timeline_old;
    ALTER INDEX IF EXISTS idx_price_history_recorded_at RENAME TO idx_price_history_recorded_at_old;

    CREATE TABLE listing_price_history (
        history_id BIGINT NOT NULL DEFAULT nextval('listing_price_history_history_id_seq'),
        listing_key TEXT NOT NULL REFERENCES listings(listing_key) ON DELETE CASCADE ON UPDATE CASCADE,
        crawl_run_id TEXT REFERENCES crawl_runs(crawl_run_id) ON DELETE SET NULL,
        price_vnd NUMERIC(18, 0) NOT NULL,
        price_per_m2 NUMERIC(14, 0),
        price_diff_vnd NUMERIC(18, 0) NOT NULL DEFAULT 0,
        price_diff_percent NUMERIC(12, 2) NOT NULL DEFAULT 0,
        price_status VARCHAR(20) NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (history_id, recorded_at),
        CONSTRAINT chk_history_price_positive CHECK (price_vnd > 0 AND (price_per_m2 IS NULL OR price_per_m2 > 0)),
        CONSTRAINT chk_history_status CHECK (price_status IN ('initial', 'increased', 'decreased')),
        CONSTRAINT chk_price_diff_logic CHECK (
            (price_status = 'initial' AND price_diff_vnd = 0) OR
            (price_status = 'increased' AND price_diff_vnd > 0) OR
            (price_status = 'decreased' AND price_diff_vnd < 0)
        )
    ) PARTITION BY RANGE (recorded_at);
    ALTER SEQUENCE listing_price_history_history_id_seq OWNED BY listing_price_history.history_id;

    CREATE TABLE listing_price_history_default PARTITION OF listing_price_history DEFAULT;
    CREATE INDEX idx_price_history_listing_timeline ON listing_price_history(listing_key, recorded_at DESC);
    CREATE INDEX idx_price_history_recorded_at ON listing_price_history(recorded_at DESC);;

CREATE OR REPLACE FUNCTION ensure_price_history_partitions(months_ahead INT DEFAULT 2)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    month_start DATE;
    last_month DATE := (date_trunc('month', NOW()) + make_interval(months => months_ahead))::date;
    part_name TEXT;
    created INT := 0;
BEGIN
    -- Start at the oldest month that has data in DEFAULT (if any), else the current month.
    SELECT LEAST(
        date_trunc('month', NOW())::date,
        COALESCE((SELECT date_trunc('month', MIN(recorded_at))::date FROM listing_price_history_default),
                 date_trunc('month', NOW())::date)
    ) INTO month_start;

    WHILE month_start <= last_month LOOP
        part_name := format('listing_price_history_y%sm%s',
                            to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
        IF to_regclass(part_name) IS NULL THEN
            EXECUTE format('CREATE TABLE %I (LIKE listing_price_history INCLUDING DEFAULTS INCLUDING CONSTRAINTS)', part_name);
            EXECUTE format(
                'WITH moved AS (DELETE FROM listing_price_history_default
                                 WHERE recorded_at >= %L AND recorded_at < %L RETURNING *)
                 INSERT INTO %I SELECT * FROM moved',
                month_start, (month_start + INTERVAL '1 month')::date, part_name);
            EXECUTE format('ALTER TABLE listing_price_history ATTACH PARTITION %I FOR VALUES FROM (%L) TO (%L)',
                           part_name, month_start, (month_start + INTERVAL '1 month')::date);
            created := created + 1;
        END IF;
        month_start := (month_start + INTERVAL '1 month')::date;
    END LOOP;
    RETURN created;
END;
$$;;

INSERT INTO listing_price_history SELECT * FROM listing_price_history_unpartitioned;
    DROP TABLE listing_price_history_unpartitioned;
    SELECT ensure_price_history_partitions(2);;

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
CREATE INDEX IF NOT EXISTS idx_mv_steep_drops_listing ON mv_steep_price_drops_7d(listing_key);;

UPDATE alembic_version SET version_num='003_partition_history' WHERE alembic_version.version_num = '002_listing_feeds';

COMMIT;

