"""Unit tests for listing lifecycle management and expiration use case."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from realestate.application.use_cases.apply_lifecycle import ApplyLifecycleUseCase
from realestate.infrastructure.persistence.postgres.lifecycle_repo import PgLifecycleRepository
from tests.helpers import make_mock_pool


@pytest.mark.asyncio
async def test_apply_lifecycle_full_sweep_success():
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = {
        "crawl_run_id": "run_fs_01",
        "site_id": "meeyland",
        "run_mode": "full_sweep",
        "status": "completed",
        "started_at": datetime(2026, 10, 30, 0, 0, tzinfo=timezone.utc),
        "lifecycle_applied_at": None,
    }
    mock_cursor.rowcount = 5  # 5 listings incremented, 2 expired

    mock_pool = make_mock_pool(mock_cursor)
    repo = PgLifecycleRepository(mock_pool)
    uc = ApplyLifecycleUseCase(repo=repo)
    result = await uc.execute("run_fs_01")

    assert result["incremented"] == 5
    assert mock_cursor.execute.call_count == 4  # select run, increment, expire, mark applied


@pytest.mark.asyncio
async def test_apply_lifecycle_skips_incremental_run():
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = {
        "crawl_run_id": "run_inc_01",
        "site_id": "meeyland",
        "run_mode": "incremental",
        "status": "completed",
        "started_at": datetime.now(timezone.utc),
        "lifecycle_applied_at": None,
    }

    mock_pool = make_mock_pool(mock_cursor)
    repo = PgLifecycleRepository(mock_pool)
    uc = ApplyLifecycleUseCase(repo=repo)
    result = await uc.execute("run_inc_01")

    assert result == {"incremented": 0, "expired": 0}
    assert mock_cursor.execute.call_count == 1  # Only selected the run
