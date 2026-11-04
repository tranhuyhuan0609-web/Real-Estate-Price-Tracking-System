"""End to end through real Kafka: raw event -> Bronze (Mongo) + Silver/Gold (Postgres), bad event -> DLQ.

All three workers are built by AppContainer exactly as the entrypoints build them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from realestate.bootstrap import AppContainer
from realestate.domain.entities import RawEventMessage
from realestate.infrastructure.config.settings import Settings
from realestate.infrastructure.config.site_configs import load_site_config
from realestate.infrastructure.crawler.parsing.listing_parser import ListingParser
from tests.conftest import SITES_DIR, load_fixture

RAW, DLQ = "it.raw_events", "it.dlq_events"


def _settings(pg, mongo, bootstrap: str) -> Settings:
    return Settings(
        POSTGRES_HOST=pg.get_container_host_ip(),
        POSTGRES_PORT=int(pg.get_exposed_port(5432)),
        POSTGRES_USER=pg.username,
        POSTGRES_PASSWORD=pg.password,
        POSTGRES_DB=pg.dbname,
        MONGO_HOST=mongo.get_container_host_ip(),
        MONGO_PORT=int(mongo.get_exposed_port(27017)),
        MONGO_USER=mongo.username,
        MONGO_PASSWORD=mongo.password,
        MONGO_DB="it_e2e",
        KAFKA_BOOTSTRAP_SERVERS=bootstrap,
        KAFKA_RAW_TOPIC=RAW,
        KAFKA_DLQ_TOPIC=DLQ,
    )


async def _run_until(worker, done: Callable[[], Awaitable[bool]], timeout: float = 90.0) -> None:
    task = asyncio.create_task(worker.run())
    try:
        async with asyncio.timeout(timeout):
            while not await done():
                if task.done():
                    task.result()  # surface a crash
                await asyncio.sleep(0.5)
    finally:
        worker.stop()
        await task


async def test_raw_event_flows_to_bronze_silver_and_dlq(pg_container, mongo_container, kafka_bootstrap, pg_pool):
    """`pg_pool` is requested only for its teardown, which truncates the tables this test fills."""
    settings = _settings(pg_container, mongo_container, kafka_bootstrap)
    config = load_site_config(SITES_DIR / "meeyland.yaml")
    raw = load_fixture("meeyland_item.json")
    item = ListingParser(config).parse_item(raw)
    event = RawEventMessage(
        listing_key=item.listing_key, site_id="meeyland", crawl_run_id="run_e2e",
        scraped_at=datetime(2026, 11, 4, tzinfo=timezone.utc), parsed_data=item, raw_payload=raw,
    )

    admin = AIOKafkaAdminClient(bootstrap_servers=kafka_bootstrap)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(RAW, 1, 1), NewTopic(DLQ, 1, 1)])
    finally:
        await admin.close()
    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    try:
        await producer.send_and_wait(RAW, json.dumps(event.model_dump(mode="json")).encode(), key=b"ok")
        await producer.send_and_wait(RAW, json.dumps({"not": "an event"}).encode(), key=b"bad")
    finally:
        await producer.stop()

    async with AppContainer(settings) as c:
        pool = await c.open_pg()
        await c.start_publisher()
        await c.sync_site_configs_uc.execute([config])
        async with pool.connection() as conn:
            await conn.execute("INSERT INTO crawl_runs (crawl_run_id, site_id) VALUES ('run_e2e', 'meeyland')")

        db = c.mongo_client[settings.MONGO_DB]

        async def bronze_done() -> bool:
            return await db["raw_listings"].count_documents({"listing_key": item.listing_key}) == 1

        async def silver_done() -> bool:
            async with pool.connection() as conn:
                cur = await conn.execute("SELECT items_processed FROM crawl_runs WHERE crawl_run_id = 'run_e2e'")
                return (await cur.fetchone())[0] == 1

        async def dlq_done() -> bool:
            # The bad event is parked once by each consumer group (bronze and silver/gold).
            return await db["dlq_events"].count_documents({}) == 2

        await _run_until(c.bronze_worker(), bronze_done)
        await _run_until(c.silver_gold_worker(), silver_done)
        await _run_until(c.dlq_worker(), dlq_done)

        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT current_price_vnd FROM listings WHERE listing_key = %s", (item.listing_key,)
            )
            assert (await cur.fetchone())[0] == item.current_price_vnd
        groups = {d["consumer_group"] async for d in db["dlq_events"].find({})}
        assert groups == {settings.KAFKA_GROUP_RAW_WRITER, settings.KAFKA_GROUP_CLEAN_PROCESSOR}
