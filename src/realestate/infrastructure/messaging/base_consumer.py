"""Shared Kafka consume loop with at-least-once delivery and a dead-letter queue.

Subclasses say how to decode a message and how to store a batch; the base class owns
polling, batching, the sequential fallback, DLQ forwarding and offset management:

* A message that cannot be decoded, or whose storage fails with a *data* error
  (``is_poison_pill``), is published to the DLQ topic.
* A message that is neither stored nor parked in the DLQ (transient error, DLQ down) makes
  the consumer seek back to it, so it is retried. Offsets are committed only otherwise.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Generic, TypeVar, cast

from aiokafka import TopicPartition

from realestate.infrastructure.messaging.dlq import forward_to_dlq
from realestate.infrastructure.messaging.producer import AioKafkaEventPublisher

E = TypeVar("E")  # decoded event
R = TypeVar("R")  # per-event processing result

IDLE_POLLS_BEFORE_EXIT = 3


class BaseConsumer(ABC, Generic[E, R]):
    name = "consumer"

    def __init__(
        self,
        *,
        consumer_factory: Callable[[], Any],
        dlq_publisher: AioKafkaEventPublisher | None,
        dlq_topic: str,
        group_id: str,
        batch_size: int = 50,
        run_once: bool = False,
    ) -> None:
        self._consumer_factory = consumer_factory
        self.dlq_publisher = dlq_publisher
        self.dlq_topic = dlq_topic
        self.group_id = group_id
        self.batch_size = batch_size
        self.run_once = run_once
        self.shutdown_event = asyncio.Event()
        self.total_processed = 0
        self.log = logging.getLogger(f"realestate.consumer.{self.name}")

    def stop(self) -> None:
        self.shutdown_event.set()

    # ------------------------------------------------------------------ hooks
    async def setup(self) -> None:
        """Called once before consuming (indexes, connections)."""

    def decode(self, msg: Any) -> E:
        """Turn a Kafka message into an event. Raising sends the message to the DLQ."""
        return cast(E, msg.value)

    @abstractmethod
    async def process_batch(self, events: list[E]) -> list[R]:
        """Store the whole batch (one result per event, same order). Raise to fall back."""

    @abstractmethod
    async def process_one(self, event: E) -> R:
        """Store a single event; used when the batch failed."""

    def is_poison_pill(self, exc: Exception) -> bool:
        """True if ``exc`` means the data is bad (DLQ) rather than the infrastructure (retry)."""
        return False

    async def after_batch(self, results: list[tuple[E, R]]) -> None:
        """Called with the successfully stored events before offsets are committed."""

    # ------------------------------------------------------------------ loop
    async def run(self) -> None:
        await self.setup()
        consumer = self._consumer_factory()
        await consumer.start()
        self.log.info("%s consuming (group=%s).", self.name, self.group_id)
        idle_polls = 0
        try:
            while not self.shutdown_event.is_set():
                try:
                    records = await consumer.getmany(timeout_ms=1000, max_records=self.batch_size)
                    if not records:
                        idle_polls += 1
                        if self.run_once and idle_polls >= IDLE_POLLS_BEFORE_EXIT:
                            break
                        continue
                    idle_polls = 0
                    await self._handle(consumer, [m for msgs in records.values() for m in msgs])
                except asyncio.CancelledError:
                    break
                except Exception:
                    self.log.exception("Error in %s poll loop", self.name)
                    await asyncio.sleep(1.0)
        finally:
            await consumer.stop()
            self.log.info("%s stopped (processed %d).", self.name, self.total_processed)

    async def _handle(self, consumer: Any, messages: list[Any]) -> None:
        retry: list[Any] = []  # neither stored nor in the DLQ
        parked = 0
        decoded: list[tuple[E, Any]] = []

        for msg in messages:
            try:
                decoded.append((self.decode(msg), msg))
            except Exception as exc:
                self.log.error("Undecodable message at offset %d: %s", msg.offset, exc)
                if await self._park(msg, exc):
                    parked += 1
                else:
                    retry.append(msg)

        stored: list[tuple[E, R]] = []
        if decoded:
            events = [e for e, _ in decoded]
            try:
                stored = list(zip(events, await self.process_batch(events), strict=True))
            except Exception as batch_err:
                self.log.warning("Batch failed (%s); retrying events one by one.", batch_err)
                for event, msg in decoded:
                    try:
                        stored.append((event, await self.process_one(event)))
                    except Exception as exc:
                        if self.is_poison_pill(exc) and await self._park(msg, exc):
                            parked += 1
                        else:
                            self.log.warning("Will retry offset %d: %s", msg.offset, exc)
                            retry.append(msg)

        self.total_processed += len(stored)
        if stored:
            try:
                await self.after_batch(stored)
            except Exception:
                self.log.exception("after_batch hook failed")

        if retry:
            earliest: dict[tuple[str, int], int] = {}
            for m in retry:
                key = (m.topic, m.partition)
                earliest[key] = min(m.offset, earliest.get(key, m.offset))
            for (topic, partition), offset in earliest.items():
                consumer.seek(TopicPartition(topic, partition), offset)
            self.log.error("%d message(s) not stored nor parked; rewound for retry.", len(retry))
            await asyncio.sleep(1.0)
        elif stored or parked:
            # Commit even if everything went to the DLQ, or it is redelivered after a restart.
            await consumer.commit()
            self.log.info("Committed batch (%d stored, %d to DLQ, total %d).", len(stored), parked, self.total_processed)

    async def _park(self, msg: Any, exc: Exception) -> bool:
        if self.dlq_publisher is None:
            return False
        return await forward_to_dlq(self.dlq_publisher, self.dlq_topic, self.group_id, msg, exc)
