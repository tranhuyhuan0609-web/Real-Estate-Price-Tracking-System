from __future__ import annotations
import argparse
import asyncio
import logging
import sys
from typing import Optional

from realestate.bootstrap import AppContainer
from realestate.domain.entities.crawl_run import RunMode

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("realestate_cli")


async def run_crawl(site_id: str, mode: str):
    container = AppContainer()
    await container.start()
    try:
        run_mode = RunMode.FULL_SWEEP if mode == "full_sweep" else RunMode.INCREMENTAL
        logger.info("Starting crawl for '%s' in %s mode...", site_id, run_mode.value)
        run_id = await container.crawl_site_uc.execute(site_id=site_id, run_mode=run_mode)
        logger.info("Crawl completed with run_id: %s", run_id)
    finally:
        await container.close()


async def run_replay_dlq(source: str = "mongo", site_id: Optional[str] = None, limit: int = 100):
    container = AppContainer()
    await container.start()
    try:
        from realestate.infrastructure.messaging.dlq import replay_from_mongo
        logger.info("Replaying up to %d messages from DLQ (%s)...", limit, source)
        count = await replay_from_mongo(
            mongo_uri=container.settings.mongo_uri,
            db_name=container.settings.MONGO_DB,
            target_topic=container.settings.KAFKA_RAW_TOPIC,
            publisher=container.publisher,
            site_id=site_id,
            limit=limit,
        )
        logger.info("Successfully replayed %d DLQ records.", count)
    finally:
        await container.close()


async def run_reset(confirm: bool = False):
    if not confirm:
        logger.warning("Reset requires --confirm flag to avoid accidental data loss.")
        return
    container = AppContainer()
    await container.start()
    try:
        logger.info("Resetting listings, price history and bronze collections...")
        async with container.pg_pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("TRUNCATE TABLE price_history, listing_details, listings, advertisers CASCADE;")
        container.bronze_repo.raw_listings.delete_many({})
        logger.info("Reset complete.")
    finally:
        await container.close()


def main():
    parser = argparse.ArgumentParser(description="Real Estate Tracker CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl_p = subparsers.add_parser("crawl", help="Trigger a site crawl manually")
    crawl_p.add_argument("--site", required=True, help="Site ID to crawl")
    crawl_p.add_argument("--mode", default="incremental", choices=["incremental", "full_sweep"])

    dlq_p = subparsers.add_parser("replay-dlq", help="Replay Dead Letter Queue messages")
    dlq_p.add_argument("--source", default="mongo", choices=["mongo", "kafka"])
    dlq_p.add_argument("--site-id", default=None, help="Optional site ID to filter")
    dlq_p.add_argument("--limit", type=int, default=100, help="Max records to replay")

    reset_p = subparsers.add_parser("reset", help="Reset all ingested listings and price histories")
    reset_p.add_argument("--confirm", action="store_true", help="Confirm database reset")

    args = parser.parse_args()
    if args.command == "crawl":
        asyncio.run(run_crawl(args.site, args.mode))
    elif args.command == "replay-dlq":
        asyncio.run(run_replay_dlq(args.source, args.site_id, args.limit))
    elif args.command == "reset":
        asyncio.run(run_reset(args.confirm))


if __name__ == "__main__":
    main()
