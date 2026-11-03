from __future__ import annotations
import psycopg_pool

def create_pg_pool(dsn: str, min_size: int = 2, max_size: int = 10, timeout: float = 30.0) -> psycopg_pool.AsyncConnectionPool:
    return psycopg_pool.AsyncConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        open=False,
    )
