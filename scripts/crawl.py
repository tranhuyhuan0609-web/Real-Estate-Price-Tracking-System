"""CLI script to trigger real estate crawler either in dry-run mode or full streaming mode."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from src.domain.entities import RunMode
from src.domain.entities.crawler_config import CrawlerConfig
from src.infrastructure.crawler.spider_factory import create_spider
from src.infrastructure.database.config_mapping import row_to_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("crawl_runner")


def load_seed_config(seed_path: Path) -> CrawlerConfig:
    import re
    text = seed_path.read_text(encoding="utf-8")
    match = re.search(r"'(\{\s*\"request\".*\})'::jsonb", text, re.DOTALL)
    if not match:
        raise ValueError(f"Could not parse config_json from {seed_path}")
    raw_config_json = json.loads(match.group(1))
    row = {
        "site_id": "meeyland",
        "name": "Meeyland - Mua bán BĐS",
        "enabled": True,
        "crawler_type": "rest_api",
        "schedule_times": [],
        "interval_seconds": 300,
        "config_json": raw_config_json,
    }
    return row_to_config(row)


async def run_dry_run(config: CrawlerConfig, max_items: int = 5):
    """Run spider in dry-run mode, printing extracted real estate items to terminal."""
    logger.info("Starting DRY-RUN crawl for site '%s' (max_items: %d)...", config.site_id, max_items)
    spider = create_spider(config)
    spider.rate_cfg.download_delay_sec = 0.5

    count = 0
    try:
        async for parsed_item, raw_payload in spider.crawl(RunMode.INCREMENTAL):
            count += 1
            print(f"\n--- [Item #{count}] Key: {parsed_item.listing_key} ---")
            print(f"  Tiêu đề: {parsed_item.details.title}")
            print(f"  Giao dịch: {parsed_item.category.value.upper()} | Loại: {parsed_item.property_type.value}")
            ppm_str = f" ({parsed_item.current_price_per_m2:,.0f} đ/m2)" if parsed_item.current_price_per_m2 else ""
            print(f"  Giá: {parsed_item.current_price_vnd:,.0f} VNĐ{ppm_str}" if parsed_item.current_price_vnd else "  Giá: Thỏa thuận")
            print(f"  Diện tích: {parsed_item.area_m2} m2 | Mặt tiền: {parsed_item.facade_m}m")
            print(f"  Pháp lý: {parsed_item.details.legal_status}")
            addr_parts = [
                f"Phường/Xã: {parsed_item.details.ward_name}" if parsed_item.details.ward_name else None,
                f"Quận/Huyện: {parsed_item.details.district_name}" if parsed_item.details.district_name else None,
                f"Tỉnh/TP: {parsed_item.details.city_name}" if parsed_item.details.city_name else None,
            ]
            print(f"  Địa chỉ: {' | '.join(filter(None, addr_parts)) or 'N/A'}")
            pub_str = parsed_item.published_at.strftime("%Y-%m-%d %H:%M:%S") if parsed_item.published_at else "N/A"
            email_str = f" - Email: {parsed_item.advertiser.email}" if (parsed_item.advertiser and parsed_item.advertiser.email) else ""
            print(f"  Người đăng: {parsed_item.advertiser.name if parsed_item.advertiser else 'N/A'} ({parsed_item.advertiser.kind.value if parsed_item.advertiser else 'N/A'}) - SĐT: {parsed_item.advertiser.phone if parsed_item.advertiser else 'N/A'}{email_str}")
            print(f"  Ngày đăng: {pub_str} | URL: {parsed_item.details.url}")

            if count >= max_items:
                break

        logger.info("Dry-run complete. Total items previewed: %d (Pages visited: %d)", count, spider.pages_crawled)
    except Exception as exc:
        logger.error("Error during dry-run crawl: %s", exc, exc_info=True)


async def main():
    parser = argparse.ArgumentParser(description="Real Estate Crawler Runner")
    parser.add_argument("--site", default="meeyland", help="Site identifier (default: meeyland)")
    parser.add_argument("--stream", action="store_true", help="Run full crawl streaming to Kafka and recording in PostgreSQL")
    parser.add_argument("--limit", type=int, default=5, help="Number of items to preview in dry-run mode (default: 5)")
    parser.add_argument("--mode", default="incremental", choices=["incremental", "full_sweep"], help="Crawl mode")
    args = parser.parse_args()

    import psycopg_pool
    from src.infrastructure.config.main import load_settings
    from src.infrastructure.database.postgres_config_repository import PostgresConfigRepository

    settings = load_settings()

    async with psycopg_pool.AsyncConnectionPool(conninfo=settings.postgres_dsn) as pool:
        config_repo = PostgresConfigRepository(pool)
        config = await config_repo.get_config(args.site)

        if args.stream:
            from src.infrastructure.database.postgres_crawl_run_repository import PostgresCrawlRunRepository
            from src.infrastructure.kafka.producer import AioKafkaEventPublisher
            from src.application.use_cases.crawl_site_uc import CrawlSiteUseCase

            logger.info("Connecting to PostgreSQL and Kafka at %s...", settings.KAFKA_BOOTSTRAP_SERVERS)
            crawl_run_repo = PostgresCrawlRunRepository(pool)
            event_publisher = AioKafkaEventPublisher(settings.KAFKA_BOOTSTRAP_SERVERS)
            await event_publisher.start()
            try:
                uc = CrawlSiteUseCase(
                    config_repo=config_repo,
                    crawl_run_repo=crawl_run_repo,
                    event_publisher=event_publisher,
                    kafka_topic=settings.KAFKA_RAW_TOPIC,
                )
                mode = RunMode.FULL_SWEEP if args.mode == "full_sweep" else RunMode.INCREMENTAL
                run_id = await uc.execute(args.site, run_mode=mode)
                logger.info("Crawl session finished with run_id: %s", run_id)
            finally:
                await event_publisher.stop()
        else:
            await run_dry_run(config, max_items=args.limit)


if __name__ == "__main__":
    asyncio.run(main())

