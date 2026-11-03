from __future__ import annotations
import argparse
import asyncio
import json
import logging
import re
import signal
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from aiokafka import AIOKafkaConsumer
from pymongo import AsyncMongoClient, MongoClient, UpdateOne
from pymongo.errors import PyMongoError
from realestate.infrastructure.config.settings import load_settings
from realestate.infrastructure.messaging.producer import AioKafkaEventPublisher
from realestate.infrastructure.persistence.mongo.setup import init_dlq_mongo_indexes

logger = logging.getLogger(__name__)


async def forward_to_dlq(
    publisher: AioKafkaEventPublisher,
    dlq_topic: str,
    consumer_group: str,
    msg: Any,
    exc: Exception,
) -> bool:
    dlq_record = {
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "consumer_group": consumer_group,
        "topic": getattr(msg, "topic", "unknown"),
        "partition": getattr(msg, "partition", 0),
        "offset": getattr(msg, "offset", 0),
        "raw_key": msg.key.decode("utf-8", errors="replace") if isinstance(getattr(msg, "key", None), (bytes, bytearray)) else str(getattr(msg, "key", "")),
        "raw_payload": getattr(msg, "value", None),
        "error_type": exc.__class__.__name__,
        "error_message": str(exc),
    }
    try:
        await publisher.publish(
            topic=dlq_topic,
            key=dlq_record["raw_key"] or "unknown_key",
            value=dlq_record,
        )
        return True
    except Exception as err:
        logger.error("Failed to forward record to DLQ topic '%s': %s", dlq_topic, err)
        return False


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
        try:
            init_dlq_mongo_indexes(mongo_uri=self.settings.mongo_uri, db_name=self.settings.MONGO_DB)
        except Exception as exc:
            logger.warning("Could not auto-initialize MongoDB DLQ indexes: %s", exc)

        mongo_client: AsyncMongoClient = AsyncMongoClient(self.settings.mongo_uri)
        db = mongo_client[self.settings.MONGO_DB]
        collection = db["dlq_events"]

        consumer = AIOKafkaConsumer(
            self.settings.KAFKA_DLQ_TOPIC,
            bootstrap_servers=self.settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=self.settings.KAFKA_GROUP_DLQ_WRITER,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        await consumer.start()

        batch: List[Dict[str, Any]] = []
        last_flush = asyncio.get_running_loop().time()
        empty_polls = 0
        total_ingested = 0

        async def flush():
            nonlocal last_flush, total_ingested
            if not batch:
                return
            count = len(batch)
            ops = [UpdateOne({"_id": d.get("_id") or f"{d.get('kafka_topic', 'dlq')}_{d.get('kafka_partition', 0)}_{d.get('kafka_offset', i)}"}, {"$setOnInsert": d}, upsert=True) for i, d in enumerate(batch)]
            try:
                await collection.bulk_write(ops, ordered=False)
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
                            doc.setdefault("ingested_at", datetime.now(timezone.utc).isoformat())
                            doc.setdefault("kafka_topic", msg.topic)
                            doc.setdefault("kafka_partition", msg.partition)
                            doc.setdefault("kafka_offset", msg.offset)
                            doc["_id"] = f"{msg.topic}_{msg.partition}_{msg.offset}"
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
            await consumer.stop()
            await mongo_client.close()


DlqEventConsumerService = DLQEventConsumerService


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

                    if error_type and val.get("error_type") != error_type:
                        continue

                    raw_payload = val.get("raw_payload") or val
                    raw_key = val.get("raw_key") or (msg.key.decode("utf-8") if msg.key else "unknown_key")

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
