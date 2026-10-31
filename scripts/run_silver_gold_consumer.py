"""Worker script to run Silver & Gold Consumer (Kafka -> PostgreSQL relational & gold price history)."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from aiokafka import AIOKafkaConsumer
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

from src.application.use_cases.process_silver_gold_uc import ProcessSilverGoldUseCase
from src.domain.entities import RawEventMessage
from src.infrastructure.config.main import load_settings
from src.infrastructure.database.pg_listing_repo import PgListingRepository
from src.infrastructure.kafka.producer import AioKafkaEventPublisher
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("silver_gold_consumer")


async def main():
    parser = argparse.ArgumentParser(description="Silver & Gold Consumer Worker")
    parser.add_argument("--once", action="store_true", help="Process available messages and exit when idle")
    args = parser.parse_args()

    settings = load_settings()
    logger.info("Initializing Silver & Gold Consumer...")
    logger.info("Kafka: %s | Topic: %s | Group: %s | DLQ: %s", settings.KAFKA_BOOTSTRAP_SERVERS, settings.KAFKA_RAW_TOPIC, settings.KAFKA_GROUP_SILVER_GOLD, settings.KAFKA_DLQ_TOPIC)
    logger.info("PostgreSQL host: %s | DB: %s", settings.POSTGRES_HOST, settings.POSTGRES_DB)

    dlq_publisher = AioKafkaEventPublisher(settings.KAFKA_BOOTSTRAP_SERVERS)
    await dlq_publisher.start()

    async with psycopg_pool.AsyncConnectionPool(conninfo=settings.postgres_dsn) as pool:
        repo = PgListingRepository(pool)
        use_case = ProcessSilverGoldUseCase(pg_repo=repo)

        consumer = AIOKafkaConsumer(
            settings.KAFKA_RAW_TOPIC,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=settings.KAFKA_GROUP_SILVER_GOLD,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )

        await consumer.start()
        logger.info("Silver & Gold consumer started and listening for events...")

        empty_polls = 0
        total_processed = 0

        try:
            while True:
                records = await consumer.getmany(timeout_ms=1000, max_records=20)
                if not records:
                    empty_polls += 1
                    if args.once and empty_polls >= 3:
                        logger.info("Silver & Gold consumer: Topic caught up and idle (total processed: %d). Exiting --once mode.", total_processed)
                        break
                    continue
                else:
                    empty_polls = 0

                batch_metrics: dict[str, dict[str, int]] = {}
                batch_count = 0

                valid_events = []
                valid_msgs = []
                for tp, messages in records.items():
                    for msg in messages:
                        try:
                            event = RawEventMessage.model_validate(msg.value)
                            valid_events.append(event)
                            valid_msgs.append(msg)
                        except Exception as exc:
                            logger.error("Validation error at offset %d: %s. Forwarding to DLQ...", msg.offset, exc)
                            dlq_record = {
                                "failed_at": datetime.now(timezone.utc).isoformat(),
                                "consumer_group": settings.KAFKA_GROUP_SILVER_GOLD,
                                "topic": msg.topic,
                                "partition": msg.partition,
                                "offset": msg.offset,
                                "raw_key": msg.key.decode("utf-8", errors="replace") if isinstance(msg.key, (bytes, bytearray)) else str(msg.key or ""),
                                "raw_payload": msg.value,
                                "error_type": exc.__class__.__name__,
                                "error_message": str(exc),
                            }
                            try:
                                await dlq_publisher.publish(
                                    topic=settings.KAFKA_DLQ_TOPIC,
                                    key=dlq_record["raw_key"] or "unknown_key",
                                    value=dlq_record,
                                )
                            except Exception as pub_err:
                                logger.error("Failed to publish error to DLQ: %s", pub_err)

                processed_results = []
                if valid_events:
                    try:
                        processed_results = await use_case.execute_batch(valid_events)
                    except Exception as batch_err:
                        logger.warning("Batch ingestion error: %s. Falling back to per-item...", batch_err)
                        for event, msg in zip(valid_events, valid_msgs):
                            try:
                                is_new, price_changed = await use_case.execute(event, update_crawl_run=False)
                                processed_results.append((event, is_new, price_changed))
                            except Exception as exc:
                                logger.error("Error processing listing '%s': %s", event.listing_key, exc)
                                dlq_record = {
                                    "failed_at": datetime.now(timezone.utc).isoformat(),
                                    "consumer_group": settings.KAFKA_GROUP_SILVER_GOLD,
                                    "topic": msg.topic,
                                    "partition": msg.partition,
                                    "offset": msg.offset,
                                    "raw_key": msg.key.decode("utf-8", errors="replace") if isinstance(msg.key, (bytes, bytearray)) else str(msg.key or ""),
                                    "raw_payload": msg.value,
                                    "error_type": exc.__class__.__name__,
                                    "error_message": str(exc),
                                }
                                try:
                                    await dlq_publisher.publish(
                                        topic=settings.KAFKA_DLQ_TOPIC,
                                        key=dlq_record["raw_key"] or "unknown_key",
                                        value=dlq_record,
                                    )
                                except Exception as pub_err:
                                    logger.error("Failed to publish error message to DLQ: %s", pub_err)

                for event, is_new, price_changed in processed_results:
                    total_processed += 1
                    batch_count += 1
                    if event.crawl_run_id:
                        m = batch_metrics.setdefault(event.crawl_run_id, {"items": 0, "new": 0, "price": 0})
                        m["items"] += 1
                        if is_new:
                            m["new"] += 1
                        if price_changed:
                            m["price"] += 1
                    logger.info(
                        "Processed listing '%s': is_new=%s, price_changed=%s (total=%d)",
                        event.listing_key, is_new, price_changed, total_processed
                    )

                # Batch update crawl_run metrics and commit Kafka offset once per batch
                for run_id, metrics in batch_metrics.items():
                    try:
                        await repo.batch_update_crawl_run_metrics(
                            run_id,
                            items_processed=metrics["items"],
                            new_listings_count=metrics["new"],
                            price_changes_detected=metrics["price"],
                        )
                    except Exception as metric_err:
                        logger.warning("Could not batch update crawl_run metrics for '%s': %s", run_id, metric_err)

                if batch_count > 0:
                    await consumer.commit()
        except asyncio.CancelledError:
            pass
        finally:
            await consumer.stop()
            await dlq_publisher.stop()
            logger.info("Silver & Gold consumer shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())


