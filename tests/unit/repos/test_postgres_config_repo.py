"""Unit tests for PostgresConfigRepository."""

from unittest.mock import AsyncMock

import pytest

from realestate.domain.exceptions import ConfigNotFoundError
from realestate.infrastructure.persistence.postgres.config_repo import PostgresConfigRepository
from tests.helpers import make_mock_pool


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
