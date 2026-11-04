"""Composition root: the one place that turns Settings into real connections and use cases.

Everything is built lazily, so a process only opens what it touches: `realestate sync-configs`
needs PostgreSQL only, `replay-dlq --source mongo` needs Mongo + Kafka, and so on.

    async with AppContainer() as c:
        await c.crawl_site_uc.execute("meeyland")
"""

from __future__ import annotations

import json
import logging
from functools import cached_property
from types import TracebackType

from aiokafka import AIOKafkaConsumer
from psycopg_pool import AsyncConnectionPool
from pymongo import AsyncMongoClient

from realestate.application.use_cases.apply_lifecycle import ApplyLifecycleUseCase
from realestate.application.use_cases.crawl_site import CrawlSiteUseCase
from realestate.application.use_cases.ingest_silver_gold import ProcessSilverGoldUseCase
from realestate.application.use_cases.reveal_phone import RevealPhoneUseCase
from realestate.application.use_cases.store_bronze import StoreBronzeUseCase
from realestate.application.use_cases.sync_site_configs import SyncSiteConfigsUseCase
from realestate.infrastructure.config.settings import Settings, load_settings
from realestate.infrastructure.crawler.spiders.phone_revealer import PhoneRevealer
from realestate.infrastructure.crawler.spiders.spider_factory import create_spider
from realestate.infrastructure.messaging.clean_consumer import CleanEventProcessorService
from realestate.infrastructure.messaging.dlq_consumer import DLQEventConsumerService
from realestate.infrastructure.messaging.producer import AioKafkaEventPublisher
from realestate.infrastructure.messaging.raw_consumer import RawEventConsumerService
from realestate.infrastructure.observability.metrics import Heartbeat
from realestate.infrastructure.persistence.mongo.bronze_repo import MongoBronzeRepository
from realestate.infrastructure.persistence.mongo.setup import (
    init_dlq_mongo_indexes,
    init_mongo_indexes,
)
from realestate.infrastructure.persistence.postgres.config_repo import PostgresConfigRepository
from realestate.infrastructure.persistence.postgres.connection import create_pg_pool
from realestate.infrastructure.persistence.postgres.crawl_run_repo import PostgresCrawlRunRepository
from realestate.infrastructure.persistence.postgres.leader_lock import PgAdvisoryLeaderLock
from realestate.infrastructure.persistence.postgres.lifecycle_repo import PgLifecycleRepository
from realestate.infrastructure.persistence.postgres.listing_repo import PgListingRepository
from realestate.infrastructure.persistence.postgres.phone_repo import PgPhoneListingRepository

logger = logging.getLogger(__name__)


class AppContainer:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._pg_opened = False
        self._publisher_started = False

    # ------------------------------------------------------------- lifecycle
    async def __aenter__(self) -> AppContainer:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        """Backwards-compatible eager start: open PostgreSQL and the Kafka producer."""
        await self.open_pg()
        await self.start_publisher()

    async def open_pg(self) -> AsyncConnectionPool:
        if not self._pg_opened:
            await self.pg_pool.open()
            self._pg_opened = True
        return self.pg_pool

    async def start_publisher(self) -> AioKafkaEventPublisher:
        if not self._publisher_started:
            await self.publisher.start()
            self._publisher_started = True
        return self.publisher

    async def close(self) -> None:
        if self._publisher_started:
            await self.publisher.stop()
        if self._pg_opened:
            await self.pg_pool.close()
        if "bronze_repo" in self.__dict__:
            await self.bronze_repo.close()
        if "leader_lock" in self.__dict__:
            await self.leader_lock.close()
        if "mongo_client" in self.__dict__:
            await self.mongo_client.close()
        logger.debug("AppContainer closed.")

    # ------------------------------------------------------------- connections
    @cached_property
    def pg_pool(self) -> AsyncConnectionPool:
        return create_pg_pool(self.settings.postgres_dsn)

    @cached_property
    def publisher(self) -> AioKafkaEventPublisher:
        return AioKafkaEventPublisher(
            self.settings.KAFKA_BOOTSTRAP_SERVERS, security=self.settings.kafka_client_kwargs()
        )

    @cached_property
    def leader_lock(self) -> PgAdvisoryLeaderLock:
        """Scheduler leadership (dedicated connection, outside the pool)."""
        return PgAdvisoryLeaderLock(self.settings.postgres_dsn)

    @cached_property
    def mongo_client(self) -> AsyncMongoClient:
        return AsyncMongoClient(self.settings.mongo_uri)

    def kafka_consumer(self, topic: str, group_id: str) -> AIOKafkaConsumer:
        """A new (not yet started) consumer with manual commits, reading JSON values."""
        return AIOKafkaConsumer(
            topic,
            **self.settings.kafka_client_kwargs(),
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )

    # ------------------------------------------------------------- repositories
    @cached_property
    def config_repo(self) -> PostgresConfigRepository:
        return PostgresConfigRepository(self.pg_pool)

    @cached_property
    def crawl_run_repo(self) -> PostgresCrawlRunRepository:
        return PostgresCrawlRunRepository(self.pg_pool)

    @cached_property
    def listing_repo(self) -> PgListingRepository:
        return PgListingRepository(self.pg_pool)

    @cached_property
    def lifecycle_repo(self) -> PgLifecycleRepository:
        return PgLifecycleRepository(self.pg_pool)

    @cached_property
    def bronze_repo(self) -> MongoBronzeRepository:
        return MongoBronzeRepository(
            mongo_uri=self.settings.mongo_uri, db_name=self.settings.MONGO_DB
        )

    # ------------------------------------------------------------- use cases
    @cached_property
    def crawl_site_uc(self) -> CrawlSiteUseCase:
        return CrawlSiteUseCase(
            config_repo=self.config_repo,
            crawl_run_repo=self.crawl_run_repo,
            event_publisher=self.publisher,
            kafka_topic=self.settings.KAFKA_RAW_TOPIC,
            spider_factory=create_spider,
        )

    @cached_property
    def ingest_silver_gold_uc(self) -> ProcessSilverGoldUseCase:
        return ProcessSilverGoldUseCase(pg_repo=self.listing_repo)

    @cached_property
    def store_bronze_uc(self) -> StoreBronzeUseCase:
        return StoreBronzeUseCase(bronze_repo=self.bronze_repo)

    @cached_property
    def apply_lifecycle_uc(self) -> ApplyLifecycleUseCase:
        return ApplyLifecycleUseCase(repo=self.lifecycle_repo)

    @cached_property
    def sync_site_configs_uc(self) -> SyncSiteConfigsUseCase:
        return SyncSiteConfigsUseCase(self.config_repo)

    @cached_property
    def phone_revealer(self) -> PhoneRevealer:
        return PhoneRevealer()

    @cached_property
    def phone_repo(self) -> PgPhoneListingRepository:
        return PgPhoneListingRepository(self.pg_pool)

    @cached_property
    def reveal_phone_uc(self) -> RevealPhoneUseCase:
        return RevealPhoneUseCase(repo=self.phone_repo, revealer=self.phone_revealer)

    # ------------------------------------------------------------- workers
    def bronze_worker(
        self, *, batch_size: int = 50, run_once: bool = False
    ) -> RawEventConsumerService:
        st = self.settings
        return RawEventConsumerService(
            use_case=self.store_bronze_uc,
            consumer_factory=lambda: self.kafka_consumer(
                st.KAFKA_RAW_TOPIC, st.KAFKA_GROUP_RAW_WRITER
            ),
            dlq_publisher=self.publisher,
            dlq_topic=st.KAFKA_DLQ_TOPIC,
            group_id=st.KAFKA_GROUP_RAW_WRITER,
            init_indexes=lambda: init_mongo_indexes(st.mongo_uri, st.MONGO_DB),
            batch_size=batch_size,
            run_once=run_once,
            heartbeat=Heartbeat("bronze", st.HEARTBEAT_DIR),
        )

    def silver_gold_worker(
        self, *, batch_size: int = 20, run_once: bool = False
    ) -> CleanEventProcessorService:
        st = self.settings
        return CleanEventProcessorService(
            use_case=self.ingest_silver_gold_uc,
            listing_repo=self.listing_repo,
            consumer_factory=lambda: self.kafka_consumer(
                st.KAFKA_RAW_TOPIC, st.KAFKA_GROUP_CLEAN_PROCESSOR
            ),
            dlq_publisher=self.publisher,
            dlq_topic=st.KAFKA_DLQ_TOPIC,
            group_id=st.KAFKA_GROUP_CLEAN_PROCESSOR,
            batch_size=batch_size,
            run_once=run_once,
            heartbeat=Heartbeat("silver_gold", st.HEARTBEAT_DIR),
        )

    def dlq_worker(
        self, *, batch_size: int = 50, run_once: bool = False
    ) -> DLQEventConsumerService:
        st = self.settings
        return DLQEventConsumerService(
            collection=self.mongo_client[st.MONGO_DB]["dlq_events"],
            consumer_factory=lambda: self.kafka_consumer(
                st.KAFKA_DLQ_TOPIC, st.KAFKA_GROUP_DLQ_WRITER
            ),
            group_id=st.KAFKA_GROUP_DLQ_WRITER,
            init_indexes=lambda: init_dlq_mongo_indexes(st.mongo_uri, st.MONGO_DB),
            batch_size=batch_size,
            run_once=run_once,
            heartbeat=Heartbeat("dlq", st.HEARTBEAT_DIR),
        )
