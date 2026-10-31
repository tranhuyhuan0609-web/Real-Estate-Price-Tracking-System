from contextlib import asynccontextmanager
import math
from typing import TYPE_CHECKING, Any, AsyncIterator
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from src.application.exceptions.config_exceptions import (
    ConfigAlreadyExistsError,
    ConfigNotFoundError,
)
from src.application.interfaces.config_repository import ConfigRepository
from src.domain.entities.crawler_config import CrawlerConfig
from src.infrastructure.database.config_mapping import (
    COLUMN_FIELDS,
    config_to_row,
    row_to_config,
)

if TYPE_CHECKING:
    from psycopg import AsyncCursor
    from psycopg_pool import AsyncConnectionPool

_SELECT_COLUMNS = (*COLUMN_FIELDS, "config_json")
_WRITE_COLUMNS = tuple(c for c in _SELECT_COLUMNS if c != "site_id")


class PostgresConfigRepository(ConfigRepository):
    """Async PostgreSQL repository for crawler configurations."""

    def __init__(
        self,
        pool: "AsyncConnectionPool",
        table_name: str = "crawler_configs",
        query_timeout: float = 5.0,
        schema: str | None = None,
    ) -> None:
        if pool is None:
            raise ValueError("Pool cannot be None.")

        if not isinstance(table_name, str) or not table_name.strip():
            raise ValueError("Table name must be a non-empty string.")

        if schema is not None and (
            not isinstance(schema, str) or not schema.strip()
        ):
            raise ValueError("Schema must be a non-empty string or None.")

        if (
            isinstance(query_timeout, bool)
            or not isinstance(query_timeout, (int, float))
            or not math.isfinite(query_timeout)
            or query_timeout <= 0
        ):
            raise ValueError("Query timeout must be a finite positive number.")

        self._pool = pool
        self._query_timeout = float(query_timeout)
        self._timeout_ms = str(max(1, math.ceil(self._query_timeout * 1000)))

        table_parts = []
        if schema is not None:
            table_parts.append(schema.strip())
        table_parts.append(table_name.strip())
        table = sql.Identifier(*table_parts)

        select_list = sql.SQL(", ").join(sql.Identifier(c) for c in _SELECT_COLUMNS)
        write_list = sql.SQL(", ").join(sql.Identifier(c) for c in _WRITE_COLUMNS)

        self._q_get = sql.SQL(
            "SELECT {cols} FROM {table} WHERE site_id = %s"
        ).format(cols=select_list, table=table)

        self._q_get_enabled = sql.SQL(
            "SELECT {cols} FROM {table} WHERE enabled = TRUE ORDER BY site_id"
        ).format(cols=select_list, table=table)

        self._q_insert = sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES ({marks}) "
            "ON CONFLICT (site_id) DO NOTHING"
        ).format(
            table=table,
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in _SELECT_COLUMNS),
            marks=sql.SQL(", ").join(sql.Placeholder() for _ in _SELECT_COLUMNS),
        )

        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(c)) for c in _WRITE_COLUMNS
        )
        self._q_update = sql.SQL(
            "UPDATE {table} SET {assignments}, updated_at = NOW() "
            "WHERE site_id = %s"
        ).format(table=table, assignments=assignments)

        self._q_delete = sql.SQL("DELETE FROM {} WHERE site_id = %s").format(table)
        self._q_update_last_run = sql.SQL(
            "UPDATE {table} SET last_run_at = NOW(), updated_at = NOW() WHERE site_id = %s"
        ).format(table=table)

    @asynccontextmanager
    async def _cursor(self) -> AsyncIterator["AsyncCursor[dict[str, Any]]"]:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (self._timeout_ms,),
                )
                async with conn.cursor(row_factory=dict_row) as cur:
                    yield cur

    @staticmethod
    def _values(config: CrawlerConfig, columns: tuple[str, ...]) -> list[Any]:
        row = config_to_row(config)
        return [
            Jsonb(row[c]) if c == "config_json" else row[c] for c in columns
        ]

    @staticmethod
    def _not_found(site_id: str) -> ConfigNotFoundError:
        return ConfigNotFoundError(f"Config with site_id '{site_id}' not found.")

    async def get_config(self, site_id: str) -> CrawlerConfig:
        async with self._cursor() as cur:
            await cur.execute(self._q_get, (site_id,))
            row = await cur.fetchone()

        if row is None:
            raise self._not_found(site_id)
        return row_to_config(row)

    async def get_enabled_configs(self) -> list[CrawlerConfig]:
        async with self._cursor() as cur:
            await cur.execute(self._q_get_enabled)
            rows = await cur.fetchall()
        return [row_to_config(row) for row in rows]

    async def create_config(self, config: CrawlerConfig) -> None:
        values = self._values(config, _SELECT_COLUMNS)

        async with self._cursor() as cur:
            await cur.execute(self._q_insert, values)
            inserted = cur.rowcount

        if inserted == 0:
            raise ConfigAlreadyExistsError(
                f"Config with site_id '{config.site_id}' already exists."
            )

    async def update_config(self, site_id: str, config: CrawlerConfig) -> None:
        if config.site_id != site_id:
            raise ValueError("Config site_id does not match the requested site_id.")

        values = [*self._values(config, _WRITE_COLUMNS), site_id]

        async with self._cursor() as cur:
            await cur.execute(self._q_update, values)
            updated = cur.rowcount

        if updated == 0:
            raise self._not_found(site_id)

    async def delete_config(self, site_id: str) -> None:
        async with self._cursor() as cur:
            await cur.execute(self._q_delete, (site_id,))
            deleted = cur.rowcount

        if deleted == 0:
            raise self._not_found(site_id)

    async def update_last_run(self, site_id: str) -> None:
        async with self._cursor() as cur:
            await cur.execute(self._q_update_last_run, (site_id,))
            updated = cur.rowcount

        if updated == 0:
            raise self._not_found(site_id)
