"""Script to reset all tables and data across PostgreSQL and MongoDB."""

import asyncio
import sys
from pathlib import Path
from pymongo import MongoClient
import psycopg_pool

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from src.infrastructure.config.main import load_settings
from src.infrastructure.database.mongo_setup import init_mongo_indexes, init_dlq_mongo_indexes


async def reset_postgresql(postgres_dsn: str):
    print("\n--- [1/2] Resetting PostgreSQL (Silver & Gold Layers) ---")
    schema_sql = (BASE_DIR / "sql" / "01_schema.sql").read_text(encoding="utf-8")
    seed_sql = (BASE_DIR / "sql" / "02_seed_configs.sql").read_text(encoding="utf-8")
    views_sql = (BASE_DIR / "sql" / "03_views.sql").read_text(encoding="utf-8")

    async with psycopg_pool.AsyncConnectionPool(conninfo=postgres_dsn) as pool:
        async with pool.connection() as conn:
            # 1. Drop existing views and tables
            print("Dropping existing PostgreSQL objects...")
            await conn.execute("""
                DROP MATERIALIZED VIEW IF EXISTS mv_ward_avg_price_per_m2 CASCADE;
                DROP MATERIALIZED VIEW IF EXISTS mv_steep_price_drops_7d CASCADE;
                DROP TABLE IF EXISTS listing_price_history CASCADE;
                DROP TABLE IF EXISTS listing_details CASCADE;
                DROP TABLE IF EXISTS listings CASCADE;
                DROP TABLE IF EXISTS advertisers CASCADE;
                DROP TABLE IF EXISTS crawl_runs CASCADE;
                DROP TABLE IF EXISTS crawler_configs CASCADE;
            """)

            # 2. Re-create schema
            print("Applying sql/01_schema.sql...")
            await conn.execute(schema_sql)

            # 3. Seed crawler configs
            print("Applying sql/02_seed_configs.sql...")
            await conn.execute(seed_sql)

            # 4. Re-create analytical views
            print("Applying sql/03_views.sql...")
            await conn.execute(views_sql)

            # Verification
            async with conn.cursor() as cur:
                await cur.execute("SELECT count(*) FROM crawler_configs;")
                config_count = (await cur.fetchone())[0]
                await cur.execute("SELECT count(*) FROM listings;")
                listing_count = (await cur.fetchone())[0]
                print(f"PostgreSQL reset complete: crawler_configs={config_count}, listings={listing_count}")


def reset_mongodb(mongo_uri: str, db_name: str):
    print("\n--- [2/2] Resetting MongoDB (Bronze Layer) ---")
    mc = MongoClient(mongo_uri)
    db = mc[db_name]
    if "raw_listings" in db.list_collection_names():
        db.drop_collection("raw_listings")
        print("Dropped MongoDB collection 'raw_listings'.")
    if "dlq_events" in db.list_collection_names():
        db.drop_collection("dlq_events")
        print("Dropped MongoDB collection 'dlq_events'.")
    mc.close()

    init_mongo_indexes(mongo_uri, db_name)
    init_dlq_mongo_indexes(mongo_uri, db_name)
    print("Re-initialized MongoDB indexes for 'raw_listings' and 'dlq_events'.")


async def main():
    settings = load_settings()
    await reset_postgresql(settings.postgres_dsn)
    reset_mongodb(settings.mongo_uri, settings.MONGO_DB)
    print("\nEnvironment successfully reset to clean initial state!")


if __name__ == "__main__":
    asyncio.run(main())

