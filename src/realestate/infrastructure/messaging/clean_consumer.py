"""Silver/Gold consumer: Kafka raw_events -> PostgreSQL listings, details, price history."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from realestate.application.ports.listing_repository import ListingRepository
from realestate.application.use_cases.ingest_silver_gold import ProcessSilverGoldUseCase
from realestate.domain.entities import RawEventMessage
from realestate.infrastructure.messaging.base_consumer import BaseConsumer
from realestate.infrastructure.messaging.producer import AioKafkaEventPublisher
from realestate.infrastructure.observability.metrics import Heartbeat

Result = tuple[bool, bool]  # (is_new, price_changed)


class CleanEventProcessorService(BaseConsumer[RawEventMessage, Result]):
    name = "silver_gold"

    def __init__(
        self,
        *,
        use_case: ProcessSilverGoldUseCase,
        listing_repo: ListingRepository,
        consumer_factory: Callable[[], Any],
        dlq_publisher: AioKafkaEventPublisher,
        dlq_topic: str,
        group_id: str,
        batch_size: int = 20,
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
        self.listing_repo = listing_repo

    def decode(self, msg: Any) -> RawEventMessage:
        return RawEventMessage.model_validate(msg.value)

    async def process_batch(self, events: list[RawEventMessage]) -> list[Result]:
        return [
            (is_new, changed) for _, is_new, changed in await self.use_case.execute_batch(events)
        ]

    async def process_one(self, event: RawEventMessage) -> Result:
        return await self.use_case.execute(event, update_crawl_run=False)

    def is_poison_pill(self, exc: Exception) -> bool:
        # DataError / IntegrityError = bad row (DLQ). OperationalError, pool timeouts and
        # ProgrammingError (schema/code bug) are not the message's fault: retry.
        return isinstance(exc, (ValueError, TypeError, psycopg.DataError, psycopg.IntegrityError))

    async def after_batch(self, results: list[tuple[RawEventMessage, Result]]) -> None:
        per_run: dict[str, list[int]] = {}
        for event, (is_new, changed) in results:
            if event.crawl_run_id:
                m = per_run.setdefault(event.crawl_run_id, [0, 0, 0])
                m[0] += 1
                m[1] += int(is_new)
                m[2] += int(changed)
        for run_id, (items, new, price_changes) in per_run.items():
            try:
                await self.listing_repo.batch_update_crawl_run_metrics(
                    run_id,
                    items_processed=items,
                    new_listings_count=new,
                    price_changes_detected=price_changes,
                )
            except Exception as exc:
                self.log.warning("Could not update crawl_run metrics for '%s': %s", run_id, exc)
