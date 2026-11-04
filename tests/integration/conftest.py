"""Real PostgreSQL (PostGIS) and MongoDB via testcontainers. Run with `make test-integration`."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

if sys.platform == "win32":  # psycopg async cannot use the Proactor loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[2]
POSTGIS_IMAGE = "postgis/postgis:16-3.4-alpine"  # same image as docker-compose.yml
MONGO_IMAGE = "mongo:7.0"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.fspath).parents:
            item.add_marker(pytest.mark.integration)


def _docker_or_skip() -> None:
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Docker is not available: {exc}")


@pytest.fixture(scope="session")
def pg_container() -> Iterator[Any]:
    _docker_or_skip()
    import psycopg
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(POSTGIS_IMAGE, driver=None) as pg:
        with psycopg.connect(pg.get_connection_url(), autocommit=True) as conn:
            conn.execute((ROOT / "sql" / "init.sql").read_text(encoding="utf-8-sig"))
        yield pg


@pytest.fixture(scope="session")
def postgres_dsn(pg_container) -> str:
    return pg_container.get_connection_url()


@pytest.fixture
async def pg_pool(postgres_dsn: str) -> AsyncIterator[object]:
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(postgres_dsn, min_size=1, max_size=4, open=False) as pool:
        yield pool
        async with pool.connection() as conn:
            await conn.execute(
                "TRUNCATE listing_price_history, listing_details, listings, advertisers, "
                "crawl_runs, crawler_configs CASCADE"
            )


@pytest.fixture(scope="session")
def mongo_container() -> Iterator[Any]:
    _docker_or_skip()
    from testcontainers.community.mongodb import MongoDbContainer

    with MongoDbContainer(MONGO_IMAGE) as mongo:
        yield mongo


@pytest.fixture(scope="session")
def mongo_uri(mongo_container) -> str:
    return mongo_container.get_connection_url()


@pytest.fixture(scope="session")
def kafka_bootstrap() -> Iterator[str]:
    _docker_or_skip()
    from testcontainers.community.kafka import KafkaContainer

    with KafkaContainer().with_env("KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS", "0") as kafka:
        yield kafka.get_bootstrap_server()
