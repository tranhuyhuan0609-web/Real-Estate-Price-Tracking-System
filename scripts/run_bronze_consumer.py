"""Worker script to run Bronze Consumer (Kafka -> MongoDB raw_listings)."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from aiokafka import AIOKafkaConsumer

BASE_DIR = Path(__file__).resolve().parents[1]
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
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bronze_consumer")


async def main():
    parser = argparse.ArgumentParser(description="Bronze Consumer Worker")
    parser.add_argument("--once", action="store_true", help="Process available messages and exit when idle")
    args = parser.parse_args()

    settings = load_settings()
    logger.info("Initializing Bronze Consumer...")
    logger.info("Kafka: %s | Topic: %s | Group: %s | DLQ: %s", settings.KAFKA_BOOTSTRAP_SERVERS, settings.KAFKA_RAW_TOPIC, settings.KAFKA_GROUP_BRONZE, settings.KAFKA_DLQ_TOPIC)
    logger.info("MongoDB: %s:%d | DB: %s", settings.MONGO_HOST, settings.MONGO_PORT, settings.MONGO_DB)

    # Initialize MongoDB collection indexes
    try:
        init_mongo_indexes(mongo_uri=settings.mongo_uri, db_name=settings.MONGO_DB)
    except Exception as idx_err:
        logger.warning("Could not initialize MongoDB indexes automatically: %s", idx_err)

    dlq_publisher = AioKafkaEventPublisher(settings.KAFKA_BOOTSTRAP_SERVERS)
    await dlq_publisher.start()

    repo = MongoBronzeRepository(mongo_uri=settings.mongo_uri, db_name=settings.MONGO_DB)
    use_case = StoreBronzeUseCase(bronze_repo=repo)

    consumer = AIOKafkaConsumer(
        settings.KAFKA_RAW_TOPIC,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=settings.KAFKA_GROUP_BRONZE,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )

    await consumer.start()
    logger.info("Bronze consumer started and listening for events...")

    batch = []
    BATCH_SIZE = 50
    FLUSH_INTERVAL_SEC = 2.0
    last_flush = asyncio.get_running_loop().time()
    empty_polls = 0

    try:
        while True:
            try:
                # Poll with short timeout to allow periodic batch flush
                records = await consumer.getmany(timeout_ms=1000, max_records=BATCH_SIZE)
                if not records:
                    empty_polls += 1
                    if args.once and empty_polls >= 3 and not batch:
                        logger.info("Bronze consumer: Topic caught up and idle. Exiting --once mode.")
                        break
                else:
                    empty_polls = 0

                for tp, messages in records.items():
                    for msg in messages:
                        try:
                            event = RawEventMessage.model_validate(msg.value)
                            batch.append(event)
                        except Exception as exc:
                            logger.error("Failed to parse RawEventMessage from Kafka at offset %d: %s. Sending to DLQ...", msg.offset, exc)
                            dlq_record = {
                                "failed_at": datetime.now(timezone.utc).isoformat(),
                                "consumer_group": settings.KAFKA_GROUP_BRONZE,
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

                now = asyncio.get_running_loop().time()
                if (len(batch) >= BATCH_SIZE) or (batch and now - last_flush >= FLUSH_INTERVAL_SEC):
                    logger.info("Flushing batch of %d items to MongoDB Bronze layer...", len(batch))
                    await use_case.process_batch(batch)
                    await consumer.commit()
                    batch.clear()
                    last_flush = now

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in bronze consumer loop: %s", exc, exc_info=True)
                await asyncio.sleep(1)
    finally:
        if batch:
            logger.info("Flushing final %d items before shutdown...", len(batch))
            await use_case.process_batch(batch)
            await consumer.commit()
        await consumer.stop()
        await dlq_publisher.stop()
        await repo.close()
        logger.info("Bronze consumer shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())

