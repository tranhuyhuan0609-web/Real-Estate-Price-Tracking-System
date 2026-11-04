"""Partition listing_price_history by month.

The table is append-only and grows with every price change, so it is range-partitioned on
``recorded_at``: old months can be detached, archived or dropped cheaply, and time-bounded
queries (the 7-day steep-drop view) only touch recent partitions.

``ensure_price_history_partitions(months_ahead)`` creates the monthly partitions from the
current month up to ``months_ahead`` months ahead. The scheduler calls it at startup and daily.
Rows that still land outside every monthly partition go to a DEFAULT partition. When a month is
created later, its rows are moved out of DEFAULT before the partition is attached.

Revision ID: 003_partition_history
Revises: 002_listing_feeds
Create Date: 2026-11-05 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "003_partition_history"
down_revision: str | None = "002_listing_feeds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STEEP_DROPS_VIEW = """
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
"""

ENSURE_PARTITIONS_FN = r"""
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
$$;
"""


def upgrade() -> None:
    # The view depends on the table and must be rebuilt on top of the partitioned one.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_steep_price_drops_7d;")
    op.execute("""
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
    CREATE INDEX idx_price_history_recorded_at ON listing_price_history(recorded_at DESC);
    """)
    op.execute(ENSURE_PARTITIONS_FN)
    op.execute("""
    INSERT INTO listing_price_history SELECT * FROM listing_price_history_unpartitioned;
    DROP TABLE listing_price_history_unpartitioned;
    SELECT ensure_price_history_partitions(2);
    """)
    op.execute(STEEP_DROPS_VIEW)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_steep_price_drops_7d;")
    op.execute("""
    CREATE TABLE listing_price_history_flat (LIKE listing_price_history INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
    INSERT INTO listing_price_history_flat SELECT * FROM listing_price_history;
    ALTER SEQUENCE listing_price_history_history_id_seq OWNED BY NONE;
    DROP TABLE listing_price_history CASCADE;
    DROP FUNCTION IF EXISTS ensure_price_history_partitions(INT);
    ALTER TABLE listing_price_history_flat RENAME TO listing_price_history;
    ALTER TABLE listing_price_history ADD PRIMARY KEY (history_id);
    ALTER TABLE listing_price_history
        ADD FOREIGN KEY (listing_key) REFERENCES listings(listing_key) ON DELETE CASCADE ON UPDATE CASCADE,
        ADD FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(crawl_run_id) ON DELETE SET NULL;
    ALTER SEQUENCE listing_price_history_history_id_seq OWNED BY listing_price_history.history_id;
    CREATE INDEX idx_price_history_listing_timeline ON listing_price_history(listing_key, recorded_at DESC);
    CREATE INDEX idx_price_history_recorded_at ON listing_price_history(recorded_at DESC);
    """)
    op.execute(STEEP_DROPS_VIEW)
