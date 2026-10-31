"""CLI script to replay Dead Letter Queue (DLQ) messages back into Kafka raw_events.

Supports replaying from:
1. MongoDB 'dlq_events' collection (default)
2. Kafka DLQ topic directly

Usage:
    python scripts/replay_dlq.py --source mongo --site-id mogi --limit 100
    python scripts/replay_dlq.py --source kafka --limit 50
    python scripts/replay_dlq.py --source mongo --delete-on-replay
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from aiokafka import AIOKafkaConsumer
from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parents[1]
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
from src.infrastructure.kafka.producer import AioKafkaEventPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("dlq_replay")


async def replay_from_mongo(
    *,
    mongo_uri: str,
    db_name: str,
    target_topic: str,
    publisher: AioKafkaEventPublisher,
    site_id: Optional[str] = None,
    error_type: Optional[str] = None,
    limit: int = 1000,
    delete_on_replay: bool = False,
) -> int:
    """Replay failed events stored in MongoDB dlq_events collection."""
    client: MongoClient = MongoClient(mongo_uri)
    try:
        db = client[db_name]
        collection = db["dlq_events"]

        query: Dict[str, Any] = {}
        if error_type:
            query["error_type"] = error_type
        if site_id:
            query["$or"] = [
                {"raw_payload.site_id": site_id},
                {"raw_key": {"$regex": f"^{re.escape(site_id)}_"}},
            ]

        cursor = collection.find(query).limit(limit)
        docs = list(cursor)
        logger.info("Found %d DLQ records matching criteria in MongoDB '%s.dlq_events'", len(docs), db_name)

        replayed_count = 0
        replayed_ids = []

        for doc in docs:
            raw_key = doc.get("raw_key") or "unknown_key"
            payload = doc.get("raw_payload") or doc

            # Remove MongoDB internal _id if inside payload
            if isinstance(payload, dict):
                payload.pop("_id", None)

            try:
                await publisher.publish(topic=target_topic, key=raw_key, value=payload)
                replayed_count += 1
                replayed_ids.append(doc["_id"])
                logger.debug("Replayed DLQ event: key='%s' -> topic='%s'", raw_key, target_topic)
            except Exception as exc:
                logger.error("Failed to replay doc %s: %s", doc.get("_id"), exc)

        if delete_on_replay and replayed_ids:
            del_res = collection.delete_many({"_id": {"$in": replayed_ids}})
            logger.info("Deleted %d replayed records from MongoDB dlq_events", del_res.deleted_count)

        return replayed_count
    finally:
        client.close()


async def replay_from_kafka(
    *,
    bootstrap_servers: str,
    dlq_topic: str,
    target_topic: str,
    publisher: AioKafkaEventPublisher,
    site_id: Optional[str] = None,
    error_type: Optional[str] = None,
    limit: int = 1000,
) -> int:
    """Replay failed events directly from Kafka DLQ topic."""
    consumer = AIOKafkaConsumer(
        dlq_topic,
        bootstrap_servers=bootstrap_servers,
        group_id=f"dlq_replay_tool_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )
    await consumer.start()
    replayed_count = 0
    try:
        logger.info("Listening on Kafka DLQ topic '%s'...", dlq_topic)
        while replayed_count < limit:
            records = await consumer.getmany(timeout_ms=3000, max_records=min(20, limit - replayed_count))
            if not records:
                logger.info("No more messages found on DLQ topic.")
                break

            for tp, messages in records.items():
                for msg in messages:
                    val = msg.value
                    if not isinstance(val, dict):
                        continue

                    # Filter by error_type if specified
                    if error_type and val.get("error_type") != error_type:
                        continue

                    raw_payload = val.get("raw_payload") or val
                    raw_key = val.get("raw_key") or (msg.key.decode("utf-8") if msg.key else "unknown_key")

                    # Filter by site_id if specified
                    if site_id:
                        event_site = raw_payload.get("site_id") if isinstance(raw_payload, dict) else None
                        if event_site != site_id and not raw_key.startswith(f"{site_id}_"):
                            continue

                    await publisher.publish(topic=target_topic, key=raw_key, value=raw_payload)
                    replayed_count += 1
                    logger.info("Replayed message offset %d (key=%s) -> %s", msg.offset, raw_key, target_topic)

                    if replayed_count >= limit:
                        break
        return replayed_count
    finally:
        await consumer.stop()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Replay Dead Letter Queue (DLQ) messages into Kafka raw_events")
    parser.add_argument("--source", choices=["mongo", "kafka"], default="mongo", help="DLQ data source (default: mongo)")
    parser.add_argument("--site-id", help="Filter by site_id (e.g. mogi, meeyland)")
    parser.add_argument("--error-type", help="Filter by error_type (e.g. ValidationError)")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum number of records to replay (default: 1000)")
    parser.add_argument("--delete-on-replay", action="store_true", help="Remove successfully replayed records from Mongo")
    parser.add_argument("--target-topic", help="Destination Kafka topic (defaults to KAFKA_RAW_TOPIC)")
    args = parser.parse_args()

    settings = load_settings()
    target_topic = args.target_topic or settings.KAFKA_RAW_TOPIC

    logger.info("Starting DLQ Replay...")
    logger.info("Source: %s | Target Topic: %s", args.source.upper(), target_topic)
    if args.site_id:
        logger.info("Filter site_id: %s", args.site_id)
    if args.error_type:
        logger.info("Filter error_type: %s", args.error_type)

    publisher = AioKafkaEventPublisher(settings.KAFKA_BOOTSTRAP_SERVERS)
    await publisher.start()

    try:
        if args.source == "mongo":
            count = await replay_from_mongo(
                mongo_uri=settings.MONGO_URI,
                db_name=settings.MONGO_DB,
                target_topic=target_topic,
                publisher=publisher,
                site_id=args.site_id,
                error_type=args.error_type,
                limit=args.limit,
                delete_on_replay=args.delete_on_replay,
            )
        else:
            count = await replay_from_kafka(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                dlq_topic=settings.KAFKA_DLQ_TOPIC,
                target_topic=target_topic,
                publisher=publisher,
                site_id=args.site_id,
                error_type=args.error_type,
                limit=args.limit,
            )
        logger.info("DLQ Replay completed! Successfully replayed %d messages.", count)
    finally:
        await publisher.stop()


if __name__ == "__main__":
    asyncio.run(main())

