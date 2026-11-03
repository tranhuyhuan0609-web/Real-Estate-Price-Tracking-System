"""Worker service: Raw Event Consumer (Kafka -> MongoDB Bronze raw_listings)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple, Any, List, Optional
from aiokafka import AIOKafkaConsumer, TopicPartition

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

from src.application.use_cases.store_bronze_uc import StoreBronzeUseCase
from src.domain.entities import RawEventMessage
from src.infrastructure.config.main import load_settings
from src.infrastructure.database.mongo_bronze_repo import MongoBronzeRepository
from src.infrastructure.database.mongo_setup import init_mongo_indexes
from src.infrastructure.kafka.producer import AioKafkaEventPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("raw_event_consumer")


class RawEventConsumerService:
    """Consumes raw listing events from Kafka and flushes them to MongoDB Bronze layer."""

    def __init__(
        self,
        *,
        batch_size: int = 50,
        flush_interval_sec: float = 2.0,
        run_once: bool = False,
    ) -> None:
        self.settings = load_settings()
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec
        self.run_once = run_once
        self.shutdown_event = asyncio.Event()

    def stop(self) -> None:
        """Signal the consumer loop to exit."""
        logger.info("Shutdown signal received for Raw Event Consumer.")
        self.shutdown_event.set()

    def _is_data_poison_pill(self, exc: BaseException) -> bool:
        """Data poison pills: BSON error, document too large, encoding issues, ValueError, TypeError.
        Transient errors: ServerSelectionTimeoutError, ConnectionFailure, NetworkTimeout.
        """
        try:
            import pymongo.errors
            import bson.errors
            if isinstance(
                exc,
                (
                    bson.errors.BSONError,
                    pymongo.errors.DocumentTooLarge,
                    ValueError,
                    TypeError,
                ),
            ):
                return True
        except ImportError:
            pass
        name = exc.__class__.__name__
        if any(token in name for token in ("BSON", "DocumentTooLarge", "ValidationError", "InvalidDocument")):
            return True
        return False

    async def _forward_to_dlq(self, dlq_publisher: AioKafkaEventPublisher, msg: Any, exc: Exception) -> bool:
        dlq_record = {
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "consumer_group": self.settings.KAFKA_GROUP_RAW_WRITER,
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
            return True
        except Exception as pub_err:
            logger.error("Failed to forward error to DLQ: %s", pub_err)
            return False

    async def run(self) -> None:
        logger.info("Starting Raw Event Consumer (Bronze Layer)...")
        logger.info(
            "Kafka: %s | Topic: %s | Group: %s | DLQ: %s",
            self.settings.KAFKA_BOOTSTRAP_SERVERS,
            self.settings.KAFKA_RAW_TOPIC,
            self.settings.KAFKA_GROUP_RAW_WRITER,
            self.settings.KAFKA_DLQ_TOPIC,
        )
        logger.info("MongoDB: %s:%d | DB: %s", self.settings.MONGO_HOST, self.settings.MONGO_PORT, self.settings.MONGO_DB)

        # 1. Initialize MongoDB indexes
        try:
            init_mongo_indexes(mongo_uri=self.settings.mongo_uri, db_name=self.settings.MONGO_DB)
        except Exception as exc:
            logger.warning("Could not auto-initialize MongoDB indexes: %s", exc)

        # 2. Start DLQ publisher
        dlq_publisher = AioKafkaEventPublisher(self.settings.KAFKA_BOOTSTRAP_SERVERS)
        await dlq_publisher.start()

        # 3. Initialize Bronze repo & use case
        repo = MongoBronzeRepository(mongo_uri=self.settings.mongo_uri, db_name=self.settings.MONGO_DB)
        use_case = StoreBronzeUseCase(bronze_repo=repo)

        # 4. Start Kafka Consumer
        consumer = AIOKafkaConsumer(
            self.settings.KAFKA_RAW_TOPIC,
            bootstrap_servers=self.settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=self.settings.KAFKA_GROUP_RAW_WRITER,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        await consumer.start()
        logger.info("Raw Event Consumer connected to Kafka and ready.")

        empty_polls = 0
        total_ingested = 0

        try:
            while not self.shutdown_event.is_set():
                try:
                    records = await consumer.getmany(timeout_ms=1000, max_records=self.batch_size)
                    if not records:
                        empty_polls += 1
                        if self.run_once and empty_polls >= 3:
                            logger.info("Topic caught up and idle (total: %d). Exiting --once mode.", total_ingested)
                            break
                        continue
                    else:
                        empty_polls = 0

                    valid_events: List[RawEventMessage] = []
                    valid_msgs: List[Any] = []
                    dlq_forwarded = 0
                    dlq_failed: List[Any] = []

                    for tp, messages in records.items():
                        for msg in messages:
                            try:
                                event = RawEventMessage.model_validate(msg.value)
                                valid_events.append(event)
                                valid_msgs.append(msg)
                            except Exception as exc:
                                logger.error("Invalid RawEventMessage at offset %d: %s. Forwarding to DLQ...", msg.offset, exc)
                                if await self._forward_to_dlq(dlq_publisher, msg, exc) is False:
                                    dlq_failed.append(msg)
                                else:
                                    dlq_forwarded += 1

                    ingested_in_batch = 0
                    if valid_events:
                        try:
                            await use_case.process_batch(valid_events)
                            ingested_in_batch = len(valid_events)
                            total_ingested += ingested_in_batch
                        except Exception as storage_err:
                            logger.warning(
                                "Failed to process Bronze batch (%s). Falling back to sequential execution...",
                                storage_err,
                            )
                            for event, msg in zip(valid_events, valid_msgs):
                                try:
                                    await use_case.process_event(event)
                                    ingested_in_batch += 1
                                    total_ingested += 1
                                except Exception as item_err:
                                    if self._is_data_poison_pill(item_err):
                                        logger.error(
                                            "Data poison pill in Bronze for '%s': %s. Forwarding to DLQ...",
                                            event.listing_key, item_err,
                                        )
                                        if await self._forward_to_dlq(dlq_publisher, msg, item_err) is False:
                                            dlq_failed.append(msg)
                                        else:
                                            dlq_forwarded += 1
                                    else:
                                        logger.warning(
                                            "Transient error storing '%s' to Bronze: %s. Rewinding for retry...",
                                            event.listing_key, item_err,
                                        )
                                        dlq_failed.append(msg)

                    if dlq_failed:
                        # Never commit past a message that failed both Bronze storage and DLQ
                        earliest: Dict[Tuple[str, int], int] = {}
                        for m in dlq_failed:
                            k = (m.topic, m.partition)
                            earliest[k] = min(m.offset, earliest.get(k, m.offset))
                        for (topic, partition), offset in earliest.items():
                            consumer.seek(TopicPartition(topic, partition), offset)
                        logger.error("%d message(s) could not be stored in Bronze nor sent to DLQ; rewound for retry.", len(dlq_failed))
                        await asyncio.sleep(1.0)
                    elif ingested_in_batch + dlq_forwarded > 0:
                        await consumer.commit()
                        logger.info("Committed batch (%d bronze events, %d to DLQ) (total: %d).", ingested_in_batch, dlq_forwarded, total_ingested)

                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error("Error in consumer poll loop: %s", exc, exc_info=True)
                    await asyncio.sleep(1.0)

        finally:
            await consumer.stop()
            await dlq_publisher.stop()
            await repo.close()
            logger.info("Raw Event Consumer shut down cleanly.")


def main():
    parser = argparse.ArgumentParser(description="Raw Event Consumer (Bronze Layer)")
    parser.add_argument("--once", action="store_true", help="Process available backlog and exit when idle")
    args = parser.parse_args()

    service = RawEventConsumerService(run_once=args.once)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _signal_handler():
        service.shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Signal handlers not implemented on Windows event loop
            pass

    try:
        loop.run_until_complete(service.run())
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt. Shutting down...")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
