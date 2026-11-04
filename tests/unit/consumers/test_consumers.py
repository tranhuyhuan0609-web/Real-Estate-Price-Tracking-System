"""BaseConsumer delivery semantics, exercised through the three concrete consumers."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import psycopg

from realestate.domain.entities import (
    Category,
    ListingDetailsData,
    ParsedListingItem,
    PropertyType,
    RawEventMessage,
)
from realestate.infrastructure.messaging.clean_consumer import CleanEventProcessorService
from realestate.infrastructure.messaging.dlq_consumer import DLQEventConsumerService
from realestate.infrastructure.messaging.raw_consumer import RawEventConsumerService

TOPIC = "raw"


class FakeKafkaConsumer:
    """Delivers the given batches once, then nothing (so run_once exits)."""

    def __init__(self, *batches):
        self._batches = list(batches)
        self.commits = 0
        self.seeks: list[tuple[object, int]] = []
        self.stopped = False

    async def start(self):
        pass

    async def stop(self):
        self.stopped = True

    async def getmany(self, timeout_ms, max_records):
        return {(TOPIC, 0): self._batches.pop(0)} if self._batches else {}

    async def commit(self):
        self.commits += 1

    def seek(self, tp, offset):
        self.seeks.append((tp, offset))


def _event(key: str, run_id: str = "run_1") -> RawEventMessage:
    return RawEventMessage(
        listing_key=key,
        site_id="meeyland",
        crawl_run_id=run_id,
        scraped_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
        parsed_data=ParsedListingItem(
            listing_key=key,
            source="meeyland",
            category=Category.SALE,
            property_type=PropertyType.HOUSE,
            details=ListingDetailsData(url=f"https://example.com/{key}", title="t"),
        ),
        raw_payload={"k": key},
    )


def _msg(offset: int, value) -> SimpleNamespace:
    return SimpleNamespace(topic=TOPIC, partition=0, offset=offset, key=b"k", value=value)


def _ok(offset: int, key: str, run_id: str = "run_1") -> SimpleNamespace:
    return _msg(offset, _event(key, run_id).model_dump(mode="json"))


def _bronze(kafka, *, use_case=None, dlq=None):
    return RawEventConsumerService(
        use_case=use_case or AsyncMock(),
        consumer_factory=lambda: kafka,
        dlq_publisher=dlq or AsyncMock(),
        dlq_topic="dlq",
        group_id="bronze",
        run_once=True,
    )


async def test_undecodable_message_goes_to_dlq_and_batch_commits():
    kafka = FakeKafkaConsumer([_ok(1, "a"), _msg(2, {"garbage": True})])
    dlq, uc = AsyncMock(), AsyncMock()
    await _bronze(kafka, use_case=uc, dlq=dlq).run()

    assert [e.listing_key for e in uc.process_batch.await_args.args[0]] == ["a"]
    dlq.publish.assert_awaited_once()
    assert dlq.publish.await_args.kwargs["value"]["consumer_group"] == "bronze"
    assert kafka.commits == 1 and kafka.seeks == [] and kafka.stopped


async def test_message_neither_stored_nor_parked_is_rewound_not_committed():
    kafka = FakeKafkaConsumer([_ok(5, "a"), _msg(6, "not json object")])
    dlq = AsyncMock()
    dlq.publish.side_effect = RuntimeError("kafka down")
    await _bronze(kafka, dlq=dlq).run()

    assert kafka.commits == 0
    assert [off for _, off in kafka.seeks] == [6]


async def test_batch_failure_falls_back_and_classifies_errors():
    uc = AsyncMock()
    uc.process_batch.side_effect = RuntimeError("bulk failed")
    uc.process_event.side_effect = [None, ValueError("bad doc"), ConnectionError("mongo down")]
    kafka = FakeKafkaConsumer([_ok(1, "a"), _ok(2, "b"), _ok(3, "c")])
    dlq = AsyncMock()
    await _bronze(kafka, use_case=uc, dlq=dlq).run()

    assert dlq.publish.await_count == 1  # "b" is a poison pill
    assert [off for _, off in kafka.seeks] == [3]  # "c" is retried
    assert kafka.commits == 0


async def test_silver_gold_updates_run_metrics_then_commits():
    uc = AsyncMock()
    uc.execute_batch.side_effect = lambda evs: [
        (e, e.listing_key == "a", e.listing_key == "b") for e in evs
    ]
    repo = AsyncMock()
    kafka = FakeKafkaConsumer([_ok(1, "a"), _ok(2, "b"), _ok(3, "c", run_id="run_2")])
    worker = CleanEventProcessorService(
        use_case=uc,
        listing_repo=repo,
        consumer_factory=lambda: kafka,
        dlq_publisher=AsyncMock(),
        dlq_topic="dlq",
        group_id="sg",
        run_once=True,
    )
    await worker.run()

    calls = {c.args[0]: c.kwargs for c in repo.batch_update_crawl_run_metrics.await_args_list}
    assert calls["run_1"] == {
        "items_processed": 2,
        "new_listings_count": 1,
        "price_changes_detected": 1,
    }
    assert calls["run_2"] == {
        "items_processed": 1,
        "new_listings_count": 0,
        "price_changes_detected": 0,
    }
    assert kafka.commits == 1 and worker.total_processed == 3


def test_silver_gold_poison_pill_classification():
    worker = CleanEventProcessorService(
        use_case=AsyncMock(),
        listing_repo=AsyncMock(),
        consumer_factory=MagicMock(),
        dlq_publisher=AsyncMock(),
        dlq_topic="dlq",
        group_id="sg",
    )
    assert worker.is_poison_pill(psycopg.errors.CheckViolation())
    assert worker.is_poison_pill(ValueError())
    assert not worker.is_poison_pill(psycopg.OperationalError())
    assert not worker.is_poison_pill(psycopg.ProgrammingError())


async def test_dlq_worker_archives_with_idempotent_ids():
    collection = AsyncMock()
    kafka = FakeKafkaConsumer([_msg(7, {"error_type": "X"}), _msg(8, "raw string")])
    await DLQEventConsumerService(
        collection=collection, consumer_factory=lambda: kafka, group_id="dlq_w", run_once=True
    ).run()

    ops = collection.bulk_write.await_args.args[0]
    docs = [op._doc["$setOnInsert"] for op in ops]
    assert [d["_id"] for d in docs] == ["raw_0_7", "raw_0_8"]
    assert docs[1]["raw_payload"] == "raw string"
    assert kafka.commits == 1


async def test_dlq_worker_retries_when_mongo_fails():
    collection = AsyncMock()
    collection.bulk_write.side_effect = ConnectionError("mongo down")
    kafka = FakeKafkaConsumer([_msg(7, {"error_type": "X"})])
    await DLQEventConsumerService(
        collection=collection, consumer_factory=lambda: kafka, group_id="dlq_w", run_once=True
    ).run()
    assert kafka.commits == 0 and [off for _, off in kafka.seeks] == [7]


async def test_outcomes_are_counted_in_metrics():
    from realestate.infrastructure.observability.metrics import CONSUMER_MESSAGES

    def value(outcome):
        return CONSUMER_MESSAGES.labels("bronze", outcome)._value.get()

    before = {o: value(o) for o in ("stored", "dlq", "retry")}
    kafka = FakeKafkaConsumer([_ok(1, "a"), _msg(2, {"garbage": True})])
    await _bronze(kafka).run()
    assert value("stored") - before["stored"] == 1
    assert value("dlq") - before["dlq"] == 1
    assert value("retry") == before["retry"]
