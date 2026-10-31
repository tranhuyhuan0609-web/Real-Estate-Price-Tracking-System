"""CLI script to apply lifecycle expiration and refresh materialized views."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
import psycopg_pool

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from src.application.use_cases.apply_lifecycle_uc import ApplyLifecycleUseCase
from src.infrastructure.config.main import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("lifecycle_runner")


async def main():
    parser = argparse.ArgumentParser(description="Lifecycle & Materialized Views Runner")
    parser.add_argument("--run-id", help="Crawl run ID to apply lifecycle rules for")
    parser.add_argument("--refresh-views", action="store_true", help="Refresh Gold Layer Materialized Views")
    args = parser.parse_args()

    settings = load_settings()
    async with psycopg_pool.AsyncConnectionPool(conninfo=settings.postgres_dsn) as pool:
        use_case = ApplyLifecycleUseCase(pool)

        if args.run_id:
            logger.info("Applying lifecycle for crawl run '%s'...", args.run_id)
            stats = await use_case.execute(args.run_id)
            logger.info("Lifecycle result: %s", stats)

        if args.refresh_views or not args.run_id:
            logger.info("Refreshing Gold Layer Materialized Views...")
            await use_case.refresh_gold_views()


if __name__ == "__main__":
    asyncio.run(main())

