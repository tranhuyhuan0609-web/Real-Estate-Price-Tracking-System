"""Unit tests for PostgresConfigRepository."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from src.application.exceptions.config_exceptions import ConfigNotFoundError
from src.infrastructure.database.postgres_config_repository import PostgresConfigRepository


def make_mock_pool(mock_cursor):
    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock()
    mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_conn.cursor = MagicMock()
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_pool


@pytest.mark.asyncio
async def test_update_last_run_success():
    mock_cursor = AsyncMock()
    mock_cursor.rowcount = 1

    mock_pool = make_mock_pool(mock_cursor)
    repo = PostgresConfigRepository(pool=mock_pool)

    await repo.update_last_run("meeyland")

    assert mock_cursor.execute.called
    query_str = str(mock_cursor.execute.call_args[0][0])
    assert "UPDATE" in query_str
    assert "last_run_at = NOW()" in query_str
    assert mock_cursor.execute.call_args[0][1] == ("meeyland",)


@pytest.mark.asyncio
async def test_update_last_run_not_found():
    mock_cursor = AsyncMock()
    mock_cursor.rowcount = 0

    mock_pool = make_mock_pool(mock_cursor)
    repo = PostgresConfigRepository(pool=mock_pool)

    with pytest.raises(ConfigNotFoundError, match="meeyland"):
        await repo.update_last_run("meeyland")
