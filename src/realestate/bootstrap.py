from __future__ import annotations
import logging
from realestate.infrastructure.config.settings import Settings, load_settings
from realestate.infrastructure.persistence.postgres.connection import create_pg_pool
from realestate.infrastructure.persistence.postgres.listing_repo import PgListingRepository
from realestate.infrastructure.persistence.postgres.lifecycle_repo import PgLifecycleRepository
from realestate.infrastructure.persistence.postgres.config_repo import PostgresConfigRepository
from realestate.infrastructure.persistence.postgres.crawl_run_repo import PostgresCrawlRunRepository
from realestate.infrastructure.persistence.mongo.bronze_repo import MongoBronzeRepository
from realestate.infrastructure.messaging.producer import AioKafkaEventPublisher
from realestate.application.use_cases.crawl_site import CrawlSiteUseCase
from realestate.application.use_cases.ingest_silver_gold import ProcessSilverGoldUseCase
from realestate.application.use_cases.store_bronze import StoreBronzeUseCase
from realestate.application.use_cases.apply_lifecycle import ApplyLifecycleUseCase
from realestate.infrastructure.crawler.spiders.spider_factory import create_spider

logger = logging.getLogger(__name__)

class AppContainer:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.pg_pool = create_pg_pool(self.settings.postgres_dsn)
        self.publisher = AioKafkaEventPublisher(self.settings.KAFKA_BOOTSTRAP_SERVERS)

        # Repositories
        self.config_repo = PostgresConfigRepository(self.pg_pool)
        self.crawl_run_repo = PostgresCrawlRunRepository(self.pg_pool)
        self.listing_repo = PgListingRepository(self.pg_pool)
        self.lifecycle_repo = PgLifecycleRepository(self.pg_pool)
        self.bronze_repo = MongoBronzeRepository(
            mongo_uri=self.settings.mongo_uri,
            db_name=self.settings.MONGO_DB,
        )

        # Use cases
        self.crawl_site_uc = CrawlSiteUseCase(
            config_repo=self.config_repo,
            crawl_run_repo=self.crawl_run_repo,
            event_publisher=self.publisher,
            kafka_topic=self.settings.KAFKA_RAW_TOPIC,
            spider_factory=create_spider,
        )
        self.ingest_silver_gold_uc = ProcessSilverGoldUseCase(pg_repo=self.listing_repo)
        self.store_bronze_uc = StoreBronzeUseCase(bronze_repo=self.bronze_repo)
        self.apply_lifecycle_uc = ApplyLifecycleUseCase(repo=self.lifecycle_repo)

    async def start(self) -> None:
        await self.pg_pool.open()
        await self.publisher.start()
        logger.info("AppContainer started successfully.")

    async def close(self) -> None:
        await self.publisher.stop()
        await self.pg_pool.close()
        await self.bronze_repo.close()
        logger.info("AppContainer closed.")
