"""Worker service: Clean Event Processor (Kafka raw_events -> PostgreSQL Silver/Gold layer)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from aiokafka import AIOKafkaConsumer, TopicPartition
import psycopg_pool

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("clean_event_processor")


class CleanEventProcessorService:
    """Consumes raw events from Kafka and cleans, normalizes, and upserts them into PostgreSQL Silver & Gold layers."""

    def __init__(
        self,
        *,
        batch_size: int = 20,
        run_once: bool = False,
    ) -> None:
        self.settings = load_settings()
        self.batch_size = batch_size
        self.run_once = run_once
        self.shutdown_event = asyncio.Event()

    async def _forward_to_dlq(self, dlq_publisher: AioKafkaEventPublisher, msg: Any, exc: Exception) -> bool:
        """Send a malformed or failed event to the Dead Letter Queue. Returns False if that failed."""
        dlq_record = {
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "consumer_group": self.settings.KAFKA_GROUP_CLEAN_PROCESSOR,
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
                topic=self.settings.KAFKA_DLQ_TOPIC,
                key=dlq_record["raw_key"] or "unknown_key",
                value=dlq_record,
            )
            logger.info("Message at offset %d successfully forwarded to DLQ.", msg.offset)
            return True
        except Exception as pub_err:
            logger.error("Failed to forward error to DLQ: %s", pub_err)
            return False

    async def run(self) -> None:
        logger.info("Starting Clean Event Processor (Silver & Gold Layers)...")
        logger.info(
            "Kafka: %s | Topic: %s | Group: %s | DLQ: %s",
            self.settings.KAFKA_BOOTSTRAP_SERVERS,
            self.settings.KAFKA_RAW_TOPIC,
            self.settings.KAFKA_GROUP_CLEAN_PROCESSOR,
            self.settings.KAFKA_DLQ_TOPIC,
        )
        logger.info("PostgreSQL: %s:%d | DB: %s", self.settings.POSTGRES_HOST, self.settings.POSTGRES_PORT, self.settings.POSTGRES_DB)

        # 1. Start DLQ publisher
        dlq_publisher = AioKafkaEventPublisher(self.settings.KAFKA_BOOTSTRAP_SERVERS)
        await dlq_publisher.start()

        # 2. Setup PostgreSQL connection pool & Use Case
        async with psycopg_pool.AsyncConnectionPool(conninfo=self.settings.postgres_dsn, min_size=1, max_size=5) as pool:
            repo = PgListingRepository(pool)
            use_case = ProcessSilverGoldUseCase(pg_repo=repo)

            # 3. Start Kafka Consumer
            consumer = AIOKafkaConsumer(
                self.settings.KAFKA_RAW_TOPIC,
                bootstrap_servers=self.settings.KAFKA_BOOTSTRAP_SERVERS,
                group_id=self.settings.KAFKA_GROUP_CLEAN_PROCESSOR,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            )
            await consumer.start()
            logger.info("Clean Event Processor connected to Kafka and listening for messages...")

            empty_polls = 0
            total_processed = 0

            try:
                while not self.shutdown_event.is_set():
                    try:
                        records = await consumer.getmany(timeout_ms=1000, max_records=self.batch_size)
                        if not records:
                            empty_polls += 1
                            if self.run_once and empty_polls >= 3:
                                logger.info("Topic caught up and idle (total processed: %d). Exiting --once mode.", total_processed)
                                break
                            continue
                        else:
                            empty_polls = 0

                        batch_metrics: Dict[str, Dict[str, int]] = {}
                        batch_count = 0
                        dlq_forwarded = 0
                        dlq_failed: List[Any] = []  # messages we could neither ingest nor park in the DLQ

                        # 1. Parse and validate messages
                        valid_events: List[RawEventMessage] = []
                        valid_msgs: List[Any] = []
                        for tp, messages in records.items():
                            for msg in messages:
                                try:
                                    event = RawEventMessage.model_validate(msg.value)
                                    valid_events.append(event)
                                    valid_msgs.append(msg)
                                except Exception as exc:
                                    logger.error(
                                        "Validation error at offset %d: %s. Forwarding to DLQ...",
                                        msg.offset, exc, exc_info=True,
                                    )
                                    if await self._forward_to_dlq(dlq_publisher, msg, exc) is False:
                                        dlq_failed.append(msg)
                                    else:
                                        dlq_forwarded += 1

                        # 2. Batch ingestion into PostgreSQL Silver & Gold
                        processed_results: List[Tuple[RawEventMessage, bool, bool]] = []
                        if valid_events:
                            try:
                                processed_results = await use_case.execute_batch(valid_events)
                            except Exception as batch_err:
                                logger.warning(
                                    "Batch ingestion error: %s. Falling back to sequential execution...",
                                    batch_err,
                                )
                                for event, msg in zip(valid_events, valid_msgs):
                                    try:
                                        is_new, price_changed = await use_case.execute(event, update_crawl_run=False)
                                        processed_results.append((event, is_new, price_changed))
                                    except Exception as item_err:
                                        logger.error("Error ingesting listing '%s': %s", event.listing_key, item_err)
                                        if await self._forward_to_dlq(dlq_publisher, msg, item_err) is False:
                                            dlq_failed.append(msg)
                                        else:
                                            dlq_forwarded += 1

                        # 3. Aggregate metrics
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
                            logger.debug(
                                "Processed listing '%s': is_new=%s, price_changed=%s (total=%d)",
                                event.listing_key, is_new, price_changed, total_processed,
                            )

                        # Update crawl run metrics per batch
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

                        if dlq_failed:
                            # Never commit past a message that is neither in Postgres nor in the DLQ:
                            # rewind to the earliest such offset per partition so it is retried.
                            earliest: Dict[Tuple[str, int], int] = {}
                            for m in dlq_failed:
                                k = (m.topic, m.partition)
                                earliest[k] = min(m.offset, earliest.get(k, m.offset))
                            for (topic, partition), offset in earliest.items():
                                consumer.seek(TopicPartition(topic, partition), offset)
                            logger.error("%d message(s) could not be stored or sent to the DLQ; rewound for retry.", len(dlq_failed))
                            await asyncio.sleep(1.0)
                        elif batch_count + dlq_forwarded > 0:
                            # Also commit when everything went to the DLQ, otherwise those offsets
                            # are redelivered (and duplicated in the DLQ) after a restart.
                            await consumer.commit()
                            logger.info(
                                "Committed batch (%d clean events, %d to DLQ) (total: %d).",
                                batch_count, dlq_forwarded, total_processed,
                            )

                    except asyncio.CancelledError:
                        break
                    except Exception as loop_err:
                        logger.error("Error in Clean Event Processor loop: %s", loop_err, exc_info=True)
                        await asyncio.sleep(1.0)

            finally:
                logger.info("Stopping Clean Event Processor consumer and DLQ publisher...")
                await consumer.stop()
                await dlq_publisher.stop()
                logger.info("Clean Event Processor stopped cleanly. Total events processed: %d", total_processed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean Event Processor (Kafka -> PostgreSQL Silver/Gold)")
    parser.add_argument("--batch-size", type=int, default=20, help="Batch size for processing events (default: 20)")
    parser.add_argument("--once", action="store_true", help="Process existing messages and exit when idle")
    args = parser.parse_args()

    service = CleanEventProcessorService(
        batch_size=args.batch_size,
        run_once=args.once,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal():
        logger.info("Shutdown signal received, initiating graceful exit...")
        service.shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            signal.signal(sig, lambda *_: handle_signal())

    try:
        loop.run_until_complete(service.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

