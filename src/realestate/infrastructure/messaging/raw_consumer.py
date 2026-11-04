"""Bronze consumer: Kafka raw_events -> MongoDB raw_listings."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import bson.errors
import pymongo.errors

from realestate.application.use_cases.store_bronze import StoreBronzeUseCase
from realestate.domain.entities import RawEventMessage
from realestate.infrastructure.messaging.base_consumer import BaseConsumer
from realestate.infrastructure.messaging.producer import AioKafkaEventPublisher
from realestate.infrastructure.observability.metrics import Heartbeat


class RawEventConsumerService(BaseConsumer[RawEventMessage, None]):
    name = "bronze"

    def __init__(
        self,
        *,
        use_case: StoreBronzeUseCase,
        consumer_factory: Callable[[], Any],
        dlq_publisher: AioKafkaEventPublisher,
        dlq_topic: str,
        group_id: str,
        init_indexes: Callable[[], None] | None = None,
        batch_size: int = 50,
        run_once: bool = False,
        heartbeat: Heartbeat | None = None,
    ) -> None:
        super().__init__(
            consumer_factory=consumer_factory,
            dlq_publisher=dlq_publisher,
            dlq_topic=dlq_topic,
            group_id=group_id,
            batch_size=batch_size,
            run_once=run_once,
            heartbeat=heartbeat,
        )
        self.use_case = use_case
        self._init_indexes = init_indexes

    async def setup(self) -> None:
        if self._init_indexes is not None:
            try:
                await asyncio.to_thread(self._init_indexes)
            except Exception as exc:
                self.log.warning("Could not initialise MongoDB indexes: %s", exc)

    def decode(self, msg: Any) -> RawEventMessage:
        return RawEventMessage.model_validate(msg.value)

    async def process_batch(self, events: list[RawEventMessage]) -> list[None]:
        await self.use_case.process_batch(events)
        return [None] * len(events)

    async def process_one(self, event: RawEventMessage) -> None:
        await self.use_case.process_event(event)

    def is_poison_pill(self, exc: Exception) -> bool:
        # Bad documents go to the DLQ; connection / timeout errors are retried.
        return isinstance(
            exc, (bson.errors.BSONError, pymongo.errors.DocumentTooLarge, ValueError, TypeError)
        )
