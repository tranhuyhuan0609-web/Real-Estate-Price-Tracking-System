"""Run the real Alembic migrations against a database holding pre-002 data."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]


def _alembic(pg, dbname: str, *args: str) -> None:
    env = {
        **os.environ,
        "POSTGRES_HOST": pg.get_container_host_ip(),
        "POSTGRES_PORT": str(pg.get_exposed_port(5432)),
        "POSTGRES_USER": pg.username,
        "POSTGRES_PASSWORD": pg.password,
        "POSTGRES_DB": dbname,
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, check=True, capture_output=True
    )


LEGACY_DATA = """
INSERT INTO crawler_configs (site_id, name, config_json) VALUES
  ('mogi', 'Mogi', '{}'), ('mogi_apartment', 'Mogi CH', '{}');
INSERT INTO crawl_runs (crawl_run_id, site_id) VALUES ('r1', 'mogi_apartment');
INSERT INTO listings (listing_key, source, status, missing_count, first_seen_at, last_seen_at,
                      category, property_type, current_price_vnd, price_status) VALUES
  -- seen by both feeds: the apartment-feed copy is newer and still active
  ('mogi_100', 'mogi', 'expired', 3, '2026-09-01', '2026-10-01', 'sale', 'apartment', 3e9, 'initial'),
  ('mogi_apartment_100', 'mogi_apartment', 'active', 0, '2026-08-01', '2026-11-01', 'sale', 'apartment', 3e9, 'initial'),
  -- only in the apartment feed
  ('mogi_apartment_200', 'mogi_apartment', 'active', 0, '2026-10-01', '2026-11-01', 'sale', 'apartment', 2e9, 'initial');
INSERT INTO listing_details (listing_key, url, title) VALUES
  ('mogi_100', 'https://mogi.vn/100', 'a'), ('mogi_apartment_100', 'https://mogi.vn/100', 'a'),
  ('mogi_apartment_200', 'https://mogi.vn/200', 'b');
INSERT INTO listing_price_history (listing_key, crawl_run_id, price_vnd, price_status, recorded_at) VALUES
  ('mogi_apartment_200', 'r1', 2e9, 'initial', '2026-10-01');
"""


def test_upgrade_merges_feeds_and_partitions_history(pg_container):
    admin_dsn = pg_container.get_connection_url()
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS legacy")
        conn.execute("CREATE DATABASE legacy")
    legacy_dsn = admin_dsn.rsplit("/", 1)[0] + "/legacy"

    _alembic(pg_container, "legacy", "upgrade", "001_initial")
    with psycopg.connect(legacy_dsn, autocommit=True) as conn:
        conn.execute(LEGACY_DATA)

    _alembic(pg_container, "legacy", "upgrade", "head")

    with psycopg.connect(legacy_dsn) as conn:
        rows = conn.execute(
            "SELECT listing_key, source, status, first_seen_at::date::text, last_seen_at::date::text "
            "FROM listings ORDER BY listing_key"
        ).fetchall()
        # duplicate merged into the portal row (window widened, newer status wins); unique row re-keyed
        assert rows == [
            ("mogi_100", "mogi", "active", "2026-08-01", "2026-11-01"),
            ("mogi_200", "mogi", "active", "2026-10-01", "2026-11-01"),
        ]
        feeds = conn.execute(
            "SELECT listing_key, site_id FROM listing_feeds ORDER BY 1, 2"
        ).fetchall()
        assert feeds == [
            ("mogi_100", "mogi"),
            ("mogi_100", "mogi_apartment"),
            ("mogi_200", "mogi_apartment"),
        ]
        # history followed the key change (ON UPDATE CASCADE) into a monthly partition
        hist = conn.execute(
            "SELECT listing_key, tableoid::regclass::text FROM listing_price_history"
        ).fetchall()
        assert hist == [("mogi_200", "listing_price_history_y2026m10")]
        assert conn.execute(
            "SELECT relkind FROM pg_class WHERE relname = 'listing_price_history'"
        ).fetchone() == ("p",)
        # new rows keep working after the swap (sequence re-owned, FKs in place)
        conn.execute(
            "INSERT INTO listing_price_history (listing_key, price_vnd, price_status) VALUES ('mogi_100', 1, 'initial')"
        )
