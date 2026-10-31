"""Worker service: DLQ Event Consumer (Kafka -> MongoDB Bronze dlq_events)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from aiokafka import AIOKafkaConsumer
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

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

from src.infrastructure.config.main import load_settings
from src.infrastructure.database.mongo_setup import init_dlq_mongo_indexes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("dlq_event_consumer")


class DLQEventConsumerService:
    """Consumes dead-letter queue (DLQ) events from Kafka and writes them to MongoDB dlq_events collection."""

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

    async def run(self) -> None:
        logger.info("Starting DLQ Event Consumer...")
        logger.info(
            "Kafka: %s | Topic: %s | Group: %s",
            self.settings.KAFKA_BOOTSTRAP_SERVERS,
            self.settings.KAFKA_DLQ_TOPIC,
            self.settings.KAFKA_GROUP_DLQ_WRITER,
        )
        logger.info("MongoDB: %s:%d | DB: %s (Collection: dlq_events)", self.settings.MONGO_HOST, self.settings.MONGO_PORT, self.settings.MONGO_DB)

        # 1. Initialize MongoDB DLQ indexes
        try:
            init_dlq_mongo_indexes(mongo_uri=self.settings.mongo_uri, db_name=self.settings.MONGO_DB)
        except Exception as exc:
            logger.warning("Could not auto-initialize MongoDB DLQ indexes: %s", exc)

        # 2. Initialize Async MongoDB Client
        mongo_client: AsyncMongoClient = AsyncMongoClient(self.settings.mongo_uri)
        db = mongo_client[self.settings.MONGO_DB]
        collection = db["dlq_events"]

        # 3. Start Kafka Consumer
        consumer = AIOKafkaConsumer(
            self.settings.KAFKA_DLQ_TOPIC,
            bootstrap_servers=self.settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=self.settings.KAFKA_GROUP_DLQ_WRITER,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        await consumer.start()
        logger.info("DLQ Event Consumer connected to Kafka and listening...")

        batch: List[Dict[str, Any]] = []
        last_flush = asyncio.get_running_loop().time()
        empty_polls = 0
        total_ingested = 0

        async def flush():
            nonlocal last_flush, total_ingested
            if not batch:
                return
            count = len(batch)
            to_insert = list(batch)
            logger.info("Flushing batch of %d DLQ events to MongoDB dlq_events...", count)
            try:
                await collection.insert_many(to_insert, ordered=False)
            except PyMongoError as err:
                logger.error("Failed to insert DLQ batch into MongoDB: %s", err)
                raise

            await consumer.commit()
            total_ingested += count
            batch.clear()
            last_flush = asyncio.get_running_loop().time()

        try:
            while not self.shutdown_event.is_set():
                try:
                    records = await consumer.getmany(timeout_ms=1000, max_records=self.batch_size)
                    if not records:
                        empty_polls += 1
                        if self.run_once and empty_polls >= 3:
                            if batch:
                                await flush()
                            logger.info("DLQ topic caught up and idle (total: %d). Exiting --once mode.", total_ingested)
                            break
                    else:
                        empty_polls = 0

                    for tp, messages in records.items():
                        for msg in messages:
                            payload = msg.value
                            if isinstance(payload, dict):
                                doc = dict(payload)
                            else:
                                doc = {
                                    "raw_payload": payload,
                                    "failed_at": datetime.now(timezone.utc).isoformat(),
                                }
                            # Enrich with metadata if missing
                            doc.setdefault("ingested_at", datetime.now(timezone.utc).isoformat())
                            doc.setdefault("kafka_topic", msg.topic)
                            doc.setdefault("kafka_partition", msg.partition)
                            doc.setdefault("kafka_offset", msg.offset)
                            batch.append(doc)

                    now = asyncio.get_running_loop().time()
                    if len(batch) >= self.batch_size or (batch and now - last_flush >= self.flush_interval_sec):
                        await flush()

                except asyncio.CancelledError:
                    break
                except Exception as loop_err:
                    logger.error("Error during DLQ polling loop: %s", loop_err, exc_info=True)
                    await asyncio.sleep(1.0)

            if batch:
                await flush()

        finally:
            logger.info("Shutting down DLQ consumer and closing MongoDB connection...")
            await consumer.stop()
            await mongo_client.close()
            logger.info("DLQ Event Consumer stopped cleanly. Total events written: %d", total_ingested)


def main() -> None:
    parser = argparse.ArgumentParser(description="DLQ Event Consumer (Kafka -> MongoDB dlq_events)")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for writing to MongoDB (default: 50)")
    parser.add_argument("--flush-interval", type=float, default=2.0, help="Flush interval in seconds (default: 2.0)")
    parser.add_argument("--once", action="store_true", help="Process existing messages and exit when idle")
    args = parser.parse_args()

    service = DLQEventConsumerService(
        batch_size=args.batch_size,
        flush_interval_sec=args.flush_interval,
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
            # On Windows signal handlers might not be fully supported on asyncio loop
            signal.signal(sig, lambda *_: handle_signal())

    try:
        loop.run_until_complete(service.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
