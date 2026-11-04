from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from realestate.domain.entities import RunMode
from realestate.domain.services.scheduling import SiteRunState
from realestate.entrypoints.scheduler import CrawlerSchedulerService
from realestate.infrastructure.persistence.postgres.config_mapping import row_to_config

UTC = timezone.utc


class FakeContainer:
    def __init__(self, configs, states):
        self.settings = SimpleNamespace(
            POSTGRES_HOST="pg", POSTGRES_PORT=5432, POSTGRES_DB="db", KAFKA_BOOTSTRAP_SERVERS="k:9092"
        )
        self.config_repo = SimpleNamespace(get_enabled_configs=AsyncMock(return_value=configs))
        self.crawl_run_repo = SimpleNamespace(
            get_schedule_states=AsyncMock(return_value=states),
            fail_interrupted_runs=AsyncMock(return_value=0),
        )
        self.crawl_site_uc = SimpleNamespace(execute=AsyncMock(return_value="run_1"))
        self.apply_lifecycle_uc = SimpleNamespace(
            wait_for_ingestion=AsyncMock(return_value=True),
            execute=AsyncMock(return_value={}),
            refresh_gold_views=AsyncMock(),
        )
        self.open_pg = AsyncMock()
        self.start_publisher = AsyncMock()
        self.close = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


def _cfg(seed_row, site_id, **kw):
    return row_to_config({**seed_row, "site_id": site_id, **kw})


async def test_due_sites_skip_running_and_recent(seed_row):
    now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    configs = [_cfg(seed_row, s) for s in ("new", "recent", "due", "running")]
    states = {
        "recent": SiteRunState(last_run_at=now - timedelta(seconds=50)),
        "due": SiteRunState(last_run_at=now - timedelta(seconds=400)),
        "running": SiteRunState(running=True),
    }
    scheduler = CrawlerSchedulerService(FakeContainer(configs, states))
    due = await scheduler.get_due_sites(now)
    assert [(c.site_id, m) for c, m in due] == [("new", RunMode.INCREMENTAL), ("due", RunMode.INCREMENTAL)]

    scheduler.active_crawls.add("new")  # already crawling in this process
    assert [c.site_id for c, _ in await scheduler.get_due_sites(now)] == ["due"]


async def test_same_full_sweep_slot_is_launched_once(seed_row):
    cfg = _cfg(seed_row, "s", schedule_times=["05:00"], interval_seconds=None)
    scheduler = CrawlerSchedulerService(FakeContainer([cfg], {}))
    now = datetime(2026, 11, 4, 5, 1, tzinfo=UTC)
    assert len(await scheduler.get_due_sites(now)) == 1
    # crawl_runs not updated yet (e.g. the run failed early): must not relaunch the slot
    assert await scheduler.get_due_sites(now + timedelta(minutes=1)) == []


async def test_run_once_launches_crawl_and_lifecycle(seed_row):
    cfg = _cfg(seed_row, "s", schedule_times=["05:00"], interval_seconds=None)
    container = FakeContainer([cfg], {})
    scheduler = CrawlerSchedulerService(container, run_once=True)
    scheduler.get_due_sites = AsyncMock(return_value=[(cfg, RunMode.FULL_SWEEP)])

    await scheduler.run()

    container.crawl_site_uc.execute.assert_awaited_once_with("s", run_mode=RunMode.FULL_SWEEP)
    container.apply_lifecycle_uc.execute.assert_awaited_once_with("run_1")
    container.apply_lifecycle_uc.refresh_gold_views.assert_awaited_once()
    container.close.assert_awaited()
