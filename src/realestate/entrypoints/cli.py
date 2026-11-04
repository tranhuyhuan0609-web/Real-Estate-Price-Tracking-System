"""Operator CLI: ``realestate <command>`` (or ``python -m realestate.entrypoints.cli``)."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from realestate.bootstrap import AppContainer
from realestate.domain.entities.crawl_run import RunMode
from realestate.entrypoints._runtime import configure_runtime
from realestate.infrastructure.config.site_configs import DEFAULT_SITES_DIR, load_site_configs

logger = logging.getLogger("realestate_cli")

# Silver/Gold tables wiped by `reset`. crawler_configs and crawl_runs are kept.
RESET_TABLES = ("listing_price_history", "listing_details", "listings", "advertisers")


async def run_crawl(site_id: str, mode: str) -> None:
    async with AppContainer() as c:
        await c.open_pg()
        await c.start_publisher()
        run_mode = RunMode.FULL_SWEEP if mode == "full_sweep" else RunMode.INCREMENTAL
        logger.info("Starting crawl for '%s' in %s mode...", site_id, run_mode.value)
        run_id = await c.crawl_site_uc.execute(site_id=site_id, run_mode=run_mode)
        logger.info("Crawl completed with run_id: %s", run_id)


async def run_sync_configs(directory: Path, dry_run: bool) -> None:
    """Upsert configs/sites/*.yaml into crawler_configs. Only needs PostgreSQL."""
    configs = load_site_configs(directory)
    async with AppContainer() as c:
        await c.open_pg()
        report = await c.sync_site_configs_uc.execute(configs, dry_run=dry_run)
    print(
        f"{'[dry-run] ' if dry_run else ''}{len(configs)} site configs: "
        f"created={report.created} updated={report.updated} unchanged={len(report.unchanged)}"
    )


async def run_replay_dlq(source: str, site_id: str | None, limit: int, delete: bool) -> None:
    from realestate.infrastructure.messaging.dlq import replay_from_kafka, replay_from_mongo

    async with AppContainer() as c:
        settings = c.settings
        publisher = await c.start_publisher()
        logger.info("Replaying up to %d messages from DLQ (%s)...", limit, source)
        if source == "kafka":
            count = await replay_from_kafka(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                security=settings.kafka_client_kwargs(),
                dlq_topic=settings.KAFKA_DLQ_TOPIC,
                target_topic=settings.KAFKA_RAW_TOPIC,
                publisher=publisher,
                site_id=site_id,
                limit=limit,
            )
        else:
            count = await replay_from_mongo(
                mongo_uri=settings.mongo_uri,
                db_name=settings.MONGO_DB,
                target_topic=settings.KAFKA_RAW_TOPIC,
                publisher=publisher,
                site_id=site_id,
                limit=limit,
                delete_on_replay=delete,
            )
        logger.info("Successfully replayed %d DLQ records.", count)


async def run_reset(confirm: bool) -> None:
    if not confirm:
        logger.warning("Reset requires --confirm flag to avoid accidental data loss.")
        return
    async with AppContainer() as c:
        pool = await c.open_pg()
        logger.info(
            "Resetting %s and the bronze raw_listings collection...", ", ".join(RESET_TABLES)
        )
        async with pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(f"TRUNCATE TABLE {', '.join(RESET_TABLES)} CASCADE;")
        await c.bronze_repo.collection.delete_many({})
        logger.info("Reset complete.")


async def run_reveal_phone(listing_key: str) -> None:
    async with AppContainer() as c:
        await c.open_pg()
        logger.info("Revealing phone for listing: %s...", listing_key)
        res = await c.reveal_phone_uc.execute(listing_key)
        logger.info("Reveal result: %s", res)
        print(f"Result for {listing_key}: {res}")


async def run_telegram_chat_id() -> int:
    from realestate.infrastructure.config.settings import load_settings
    from realestate.infrastructure.notifications.telegram import TelegramClient

    client = TelegramClient(load_settings().TELEGRAM_BOT_TOKEN.get_secret_value())
    chats = await client.recent_chats()
    if not chats:
        print(
            "No chats yet. Open your bot in Telegram, press Start / send any message, then rerun."
        )
        return 1
    print(
        "Chats that messaged the bot (newest first). Put the right id in .env as TELEGRAM_CHAT_ID:"
    )
    for chat in chats:
        print(f"  {chat['id']:>15}  {chat['type']:<10} {chat['name']}")
    return 0


async def run_telegram_test(message: str) -> int:
    from realestate.infrastructure.config.settings import load_settings
    from realestate.infrastructure.notifications.telegram import TelegramClient

    settings = load_settings()
    if not settings.TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID is empty; run `realestate telegram-chat-id` first.")
        return 1
    await TelegramClient(settings.TELEGRAM_BOT_TOKEN.get_secret_value()).send_message(
        settings.TELEGRAM_CHAT_ID, message
    )
    print("Sent. Check Telegram.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="realestate", description="Real Estate Tracker CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    crawl_p = sub.add_parser("crawl", help="Trigger a site crawl manually")
    crawl_p.add_argument("--site", required=True, help="Site ID to crawl")
    crawl_p.add_argument("--mode", default="incremental", choices=["incremental", "full_sweep"])

    sync_p = sub.add_parser("sync-configs", help="Upsert configs/sites/*.yaml into crawler_configs")
    sync_p.add_argument("--dir", type=Path, default=DEFAULT_SITES_DIR, help="Site config directory")
    sync_p.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing"
    )

    dlq_p = sub.add_parser("replay-dlq", help="Replay Dead Letter Queue messages")
    dlq_p.add_argument("--source", default="mongo", choices=["mongo", "kafka"])
    dlq_p.add_argument("--site-id", default=None, help="Optional site ID to filter")
    dlq_p.add_argument("--limit", type=int, default=100, help="Max records to replay")
    dlq_p.add_argument("--delete", action="store_true", help="(mongo) delete records once replayed")

    reset_p = sub.add_parser("reset", help="Reset all ingested listings and price histories")
    reset_p.add_argument("--confirm", action="store_true", help="Confirm database reset")

    phone_p = sub.add_parser("reveal-phone", help="Reveal unmasked advertiser phone for a listing")
    phone_p.add_argument("--listing-key", required=True, help="Listing key to reveal phone for")

    sub.add_parser(
        "telegram-chat-id", help="List chats that messaged the alert bot (to find TELEGRAM_CHAT_ID)"
    )
    tg_p = sub.add_parser("telegram-test", help="Send a test message to TELEGRAM_CHAT_ID")
    tg_p.add_argument(
        "--message", default="✅ Real Estate Tracker: Telegram alerts are configured."
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    configure_runtime()
    if args.command == "crawl":
        asyncio.run(run_crawl(args.site, args.mode))
    elif args.command == "sync-configs":
        asyncio.run(run_sync_configs(args.dir, args.dry_run))
    elif args.command == "replay-dlq":
        asyncio.run(run_replay_dlq(args.source, args.site_id, args.limit, args.delete))
    elif args.command == "reset":
        asyncio.run(run_reset(args.confirm))
    elif args.command == "reveal-phone":
        asyncio.run(run_reveal_phone(args.listing_key))
    elif args.command in ("telegram-chat-id", "telegram-test"):
        from realestate.infrastructure.notifications.telegram import TelegramError

        try:
            if args.command == "telegram-chat-id":
                code = asyncio.run(run_telegram_chat_id())
            else:
                code = asyncio.run(run_telegram_test(args.message))
        except TelegramError as exc:
            print(f"Telegram: {exc}")
            code = 1
        raise SystemExit(code)


if __name__ == "__main__":
    main()
