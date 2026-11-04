from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiokafka import AIOKafkaConsumer
from pymongo import AsyncMongoClient

from realestate.infrastructure.messaging.producer import AioKafkaEventPublisher

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
        "raw_key": msg.key.decode("utf-8", errors="replace")
        if isinstance(getattr(msg, "key", None), (bytes, bytearray))
        else str(getattr(msg, "key", "")),
        "raw_payload": getattr(msg, "value", None),
        "error_type": exc.__class__.__name__,
        "error_message": str(exc),
    }
    try:
        await publisher.publish(
            topic=dlq_topic,
            key=str(dlq_record["raw_key"] or "unknown_key"),
            value=dlq_record,
        )
        return True
    except Exception as err:
        logger.error("Failed to forward record to DLQ topic '%s': %s", dlq_topic, err)
        return False


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
    """Re-publish failed events archived in MongoDB ``dlq_events`` (non-blocking driver)."""
    query: Dict[str, Any] = {}
    if error_type:
        query["error_type"] = error_type
    if site_id:
        query["$or"] = [
            {"raw_payload.site_id": site_id},
            {"raw_key": {"$regex": f"^{re.escape(site_id)}_"}},
        ]

    client: AsyncMongoClient = AsyncMongoClient(mongo_uri)
    try:
        collection = client[db_name]["dlq_events"]
        replayed_ids: List[Any] = []
        async for doc in collection.find(query).limit(limit):
            raw_key = doc.get("raw_key") or "unknown_key"
            payload = doc.get("raw_payload") or {k: v for k, v in doc.items() if k != "_id"}
            if isinstance(payload, dict):
                payload.pop("_id", None)
            try:
                await publisher.publish(topic=target_topic, key=raw_key, value=payload)
                replayed_ids.append(doc["_id"])
            except Exception as exc:
                logger.error("Failed to replay doc %s: %s", doc.get("_id"), exc)

        logger.info("Replayed %d DLQ records from '%s.dlq_events'", len(replayed_ids), db_name)
        if delete_on_replay and replayed_ids:
            result = await collection.delete_many({"_id": {"$in": replayed_ids}})
            logger.info("Deleted %d replayed records from dlq_events", result.deleted_count)
        return len(replayed_ids)
    finally:
        await client.close()


async def replay_from_kafka(
    *,
    bootstrap_servers: str,
    dlq_topic: str,
    target_topic: str,
    publisher: AioKafkaEventPublisher,
    site_id: Optional[str] = None,
    error_type: Optional[str] = None,
    limit: int = 1000,
    security: Optional[Dict[str, Any]] = None,
) -> int:
    """Replay failed events directly from Kafka DLQ topic."""
    consumer = AIOKafkaConsumer(
        dlq_topic,
        bootstrap_servers=bootstrap_servers,
        **{k: v for k, v in (security or {}).items() if k != "bootstrap_servers"},
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
            records = await consumer.getmany(
                timeout_ms=3000, max_records=min(20, limit - replayed_count)
            )
            if not records:
                logger.info("No more messages found on DLQ topic.")
                break

            for _tp, messages in records.items():
                for msg in messages:
                    val = msg.value
                    if not isinstance(val, dict):
                        continue

                    if error_type and val.get("error_type") != error_type:
                        continue

                    raw_payload = val.get("raw_payload") or val
                    raw_key = val.get("raw_key") or (
                        msg.key.decode("utf-8") if msg.key else "unknown_key"
                    )

                    if site_id:
                        event_site = (
                            raw_payload.get("site_id") if isinstance(raw_payload, dict) else None
                        )
                        if event_site != site_id and not raw_key.startswith(f"{site_id}_"):
                            continue

                    await publisher.publish(topic=target_topic, key=raw_key, value=raw_payload)
                    replayed_count += 1
                    logger.info(
                        "Replayed message offset %d (key=%s) -> %s",
                        msg.offset,
                        raw_key,
                        target_topic,
                    )

                    if replayed_count >= limit:
                        break
        return replayed_count
    finally:
        await consumer.stop()
