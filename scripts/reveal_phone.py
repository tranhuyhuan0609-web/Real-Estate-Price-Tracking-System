"""CLI tool to reveal and update advertiser phone number for a listing on demand."""

import argparse
import asyncio
import json
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

from src.application.use_cases.reveal_phone_uc import RevealPhoneUseCase
from src.infrastructure.crawler.phone_revealer import PhoneRevealer
from src.infrastructure.config.main import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("reveal_phone")


async def main():
    parser = argparse.ArgumentParser(description="On-Demand Phone Reveal Tool")
    parser.add_argument("--listing-key", required=True, help="Listing key to reveal phone for (e.g. nhatot_132323949)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Timeout in seconds for network extraction")
    args = parser.parse_args()

    settings = load_settings()
    revealer = PhoneRevealer(timeout_sec=args.timeout)

    async with psycopg_pool.AsyncConnectionPool(conninfo=settings.postgres_dsn, min_size=1, max_size=5) as pool:
        use_case = RevealPhoneUseCase(pool, revealer=revealer)
        logger.info("Executing on-demand phone reveal for '%s'...", args.listing_key)
        result = await use_case.execute(args.listing_key)
        print("\n" + json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

