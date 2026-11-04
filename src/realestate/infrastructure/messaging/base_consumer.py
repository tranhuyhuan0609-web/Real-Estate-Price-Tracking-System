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
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Generic, TypeVar, cast

from aiokafka import TopicPartition

from realestate.infrastructure.messaging.dlq import forward_to_dlq
from realestate.infrastructure.messaging.producer import AioKafkaEventPublisher
from realestate.infrastructure.observability.metrics import (
    CONSUMER_BATCH_SECONDS,
    CONSUMER_LAG,
    CONSUMER_MESSAGES,
    Heartbeat,
)

E = TypeVar("E")  # decoded event
R = TypeVar("R")  # per-event processing result

IDLE_POLLS_BEFORE_EXIT = 3
LAG_INTERVAL = 10.0  # seconds between consumer-lag samples


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
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self._consumer_factory = consumer_factory
        self.heartbeat = heartbeat
        self._lag_checked = 0.0
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
                if self.heartbeat is not None:
                    self.heartbeat.beat()
                try:
                    records = await consumer.getmany(timeout_ms=1000, max_records=self.batch_size)
                    await self._record_lag(consumer)
                    if not records:
                        idle_polls += 1
                        if self.run_once and idle_polls >= IDLE_POLLS_BEFORE_EXIT:
                            break
                        continue
                    idle_polls = 0
                    with CONSUMER_BATCH_SECONDS.labels(self.name).time():
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
        decoded, parked, retry = await self._decode_all(messages)
        stored, parked_now, retry_now = await self._store(decoded)
        parked += parked_now
        retry += retry_now

        # Offsets at or after the earliest message to retry will be re-delivered by the seek
        # below, so only what lies before them counts as done (no double-counted metrics).
        rewind_to: dict[tuple[str, int], int] = {}
        for m in retry:
            key = (m.topic, m.partition)
            rewind_to[key] = min(m.offset, rewind_to.get(key, m.offset))
        done = [
            (e, r)
            for e, r, m in stored
            if m.offset < rewind_to.get((m.topic, m.partition), m.offset + 1)
        ]

        self.total_processed += len(done)
        CONSUMER_MESSAGES.labels(self.name, "stored").inc(len(done))
        CONSUMER_MESSAGES.labels(self.name, "dlq").inc(parked)
        CONSUMER_MESSAGES.labels(self.name, "retry").inc(len(retry))
        if done:
            try:
                await self.after_batch(done)
            except Exception:
                self.log.exception("after_batch hook failed")

        if rewind_to:
            for (topic, partition), offset in rewind_to.items():
                consumer.seek(TopicPartition(topic, partition), offset)
            self.log.error("%d message(s) not stored nor parked; rewound for retry.", len(retry))
            await asyncio.sleep(1.0)
        elif stored or parked:
            # Commit even if everything went to the DLQ, or it is redelivered after a restart.
            await consumer.commit()
            self.log.info(
                "Committed batch (%d stored, %d to DLQ, total %d).",
                len(stored),
                parked,
                self.total_processed,
            )

    async def _decode_all(self, messages: list[Any]) -> tuple[list[tuple[E, Any]], int, list[Any]]:
        decoded: list[tuple[E, Any]] = []
        parked, retry = 0, []
        for msg in messages:
            try:
                decoded.append((self.decode(msg), msg))
            except Exception as exc:
                self.log.error("Undecodable message at offset %d: %s", msg.offset, exc)
                if await self._park(msg, exc):
                    parked += 1
                else:
                    retry.append(msg)
        return decoded, parked, retry

    async def _store(
        self, decoded: list[tuple[E, Any]]
    ) -> tuple[list[tuple[E, R, Any]], int, list[Any]]:
        """Store as one batch; if that fails, one by one, parking poison pills in the DLQ."""
        if not decoded:
            return [], 0, []
        try:
            results = await self.process_batch([e for e, _ in decoded])
            return [(e, r, m) for (e, m), r in zip(decoded, results, strict=True)], 0, []
        except Exception as batch_err:
            self.log.warning("Batch failed (%s); retrying events one by one.", batch_err)
        stored: list[tuple[E, R, Any]] = []
        parked, retry = 0, []
        for event, msg in decoded:
            try:
                stored.append((event, await self.process_one(event), msg))
            except Exception as exc:
                if self.is_poison_pill(exc) and await self._park(msg, exc):
                    parked += 1
                else:
                    self.log.warning("Will retry offset %d: %s", msg.offset, exc)
                    retry.append(msg)
        return stored, parked, retry

    async def _record_lag(self, consumer: Any) -> None:
        """Export high-watermark lag per assigned partition, at most every LAG_INTERVAL seconds."""
        now = time.monotonic()
        if now - self._lag_checked < LAG_INTERVAL:
            return
        self._lag_checked = now
        try:
            for tp in consumer.assignment():
                highwater = consumer.highwater(tp)
                if highwater is None:
                    continue
                position = await consumer.position(tp)
                CONSUMER_LAG.labels(self.name, tp.topic, str(tp.partition)).set(
                    max(0, highwater - position)
                )
        except Exception as exc:  # metrics must never stop consumption
            self.log.debug("Could not compute consumer lag: %s", exc)

    async def _park(self, msg: Any, exc: Exception) -> bool:
        if self.dlq_publisher is None:
            return False
        return await forward_to_dlq(self.dlq_publisher, self.dlq_topic, self.group_id, msg, exc)
