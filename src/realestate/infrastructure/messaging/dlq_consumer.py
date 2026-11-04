"""DLQ archiver: Kafka dlq_events -> MongoDB dlq_events (the source of `realestate replay-dlq`)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from pymongo import UpdateOne

from realestate.infrastructure.messaging.base_consumer import BaseConsumer


class DLQEventConsumerService(BaseConsumer[dict, None]):
    """Archives DLQ records. Failures are always retried: there is no DLQ for the DLQ."""

    name = "dlq"

    def __init__(
        self,
        *,
        collection: Any,
        consumer_factory: Callable[[], Any],
        group_id: str,
        init_indexes: Callable[[], None] | None = None,
        batch_size: int = 50,
        run_once: bool = False,
    ) -> None:
        super().__init__(
            consumer_factory=consumer_factory,
            dlq_publisher=None,
            dlq_topic="",
            group_id=group_id,
            batch_size=batch_size,
            run_once=run_once,
        )
        self.collection = collection
        self._init_indexes = init_indexes

    async def setup(self) -> None:
        if self._init_indexes is not None:
            try:
                await asyncio.to_thread(self._init_indexes)
            except Exception as exc:
                self.log.warning("Could not initialise MongoDB DLQ indexes: %s", exc)

    def decode(self, msg: Any) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        doc = dict(msg.value) if isinstance(msg.value, dict) else {"raw_payload": msg.value, "failed_at": now}
        doc.setdefault("ingested_at", now)
        doc.setdefault("kafka_topic", msg.topic)
        doc.setdefault("kafka_partition", msg.partition)
        doc.setdefault("kafka_offset", msg.offset)
        doc["_id"] = f"{msg.topic}_{msg.partition}_{msg.offset}"  # idempotent on redelivery
        return doc

    async def process_batch(self, events: list[dict]) -> list[None]:
        ops = [UpdateOne({"_id": d["_id"]}, {"$setOnInsert": d}, upsert=True) for d in events]
        await self.collection.bulk_write(ops, ordered=False)
        return [None] * len(events)

    async def process_one(self, event: dict) -> None:
        await self.process_batch([event])
