"""init.sql + config sync + Silver/Gold ingestion against a real PostGIS database."""

from datetime import datetime, timedelta, timezone

from realestate.application.use_cases.ingest_silver_gold import ProcessSilverGoldUseCase
from realestate.application.use_cases.sync_site_configs import SyncSiteConfigsUseCase
from realestate.domain.entities import RawEventMessage
from realestate.entrypoints.cli import RESET_TABLES
from realestate.infrastructure.config.site_configs import load_site_config, load_site_configs
from realestate.infrastructure.crawler.parsing.listing_parser import ListingParser
from realestate.infrastructure.persistence.postgres.config_repo import PostgresConfigRepository
from realestate.infrastructure.persistence.postgres.listing_repo import PgListingRepository
from tests.conftest import SITES_DIR, load_fixture


async def test_sync_configs_is_idempotent(pg_pool):
    repo = PostgresConfigRepository(pg_pool)
    configs = load_site_configs(SITES_DIR)
    uc = SyncSiteConfigsUseCase(repo)

    first = await uc.execute(configs)
    assert sorted(first.created) == sorted(c.site_id for c in configs)

    second = await uc.execute(configs)
    assert not second.changed, "a second sync must be a no-op (lossless DB round trip)"

    enabled = {c.site_id for c in await repo.get_enabled_configs()}
    assert enabled == {c.site_id for c in configs if c.enabled}


async def _seed_run(pg_pool, run_id: str) -> None:
    async with pg_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO crawl_runs (crawl_run_id, site_id) VALUES (%s, 'meeyland')", (run_id,)
        )


async def test_ingest_parsed_listing_then_price_change(pg_pool):
    config = load_site_config(SITES_DIR / "meeyland.yaml")
    await SyncSiteConfigsUseCase(PostgresConfigRepository(pg_pool)).execute([config])
    await _seed_run(pg_pool, "run_1")

    raw = load_fixture("meeyland_item.json")
    item = ListingParser(config).parse_item(raw)
    uc = ProcessSilverGoldUseCase(pg_repo=PgListingRepository(pg_pool))
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def event(parsed, at):
        return RawEventMessage(
            listing_key=parsed.listing_key, site_id="meeyland", crawl_run_id="run_1",
            scraped_at=at, parsed_data=parsed, raw_payload=raw,
        )

    assert await uc.execute(event(item, t0)) == (True, False)
    assert await uc.execute(event(item, t0 + timedelta(hours=1))) == (False, False)

    cheaper = item.model_copy(update={"current_price_vnd": 1_700_000_000})
    is_new, price_changed = await uc.execute(event(cheaper, t0 + timedelta(hours=2)))
    assert (is_new, price_changed) == (False, True)

    async with pg_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT current_price_vnd FROM listings WHERE listing_key = %s", (item.listing_key,)
        )
        assert (await cur.fetchone())[0] == 1_700_000_000
        cur = await conn.execute(
            "SELECT count(*) FROM listing_price_history WHERE listing_key = %s", (item.listing_key,)
        )
        assert (await cur.fetchone())[0] >= 2


async def test_cli_reset_statement_runs(pg_pool):
    async with pg_pool.connection() as conn:
        await conn.execute(f"TRUNCATE TABLE {', '.join(RESET_TABLES)} CASCADE;")


async def test_replayed_old_event_does_not_roll_listing_back(pg_pool):
    """A week-old DLQ replay must not revive an expired listing or restore its old price."""
    config = load_site_config(SITES_DIR / "meeyland.yaml")
    await SyncSiteConfigsUseCase(PostgresConfigRepository(pg_pool)).execute([config])
    await _seed_run(pg_pool, "run_1")

    raw = load_fixture("meeyland_item.json")
    item = ListingParser(config).parse_item(raw)
    uc = ProcessSilverGoldUseCase(pg_repo=PgListingRepository(pg_pool))
    today = datetime(2026, 11, 8, tzinfo=timezone.utc)
    week_ago = today - timedelta(days=7)

    def event(parsed, at):
        return RawEventMessage(
            listing_key=parsed.listing_key, site_id="meeyland", crawl_run_id="run_1",
            scraped_at=at, parsed_data=parsed, raw_payload=raw,
        )

    await uc.execute(event(item, today))
    async with pg_pool.connection() as conn:
        await conn.execute(
            "UPDATE listings SET status = 'expired', missing_count = 3, ended_at = %s WHERE listing_key = %s",
            (today, item.listing_key),
        )

    old_price = item.model_copy(update={"current_price_vnd": 2_000_000_000})
    assert await uc.execute(event(old_price, week_ago)) == (False, False)

    async with pg_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT status, missing_count, current_price_vnd, first_seen_at, last_seen_at "
            "FROM listings WHERE listing_key = %s",
            (item.listing_key,),
        )
        status, missing, price, first_seen, last_seen = await cur.fetchone()
        cur = await conn.execute(
            "SELECT count(*) FROM listing_price_history WHERE listing_key = %s", (item.listing_key,)
        )
        history_rows = (await cur.fetchone())[0]

    assert (status, missing) == ("expired", 3)
    assert price == item.current_price_vnd
    assert history_rows == 1  # only the initial observation
    assert (first_seen, last_seen) == (week_ago, today)  # window widened, not rolled back


async def test_schedule_states_and_interrupted_runs(pg_pool):
    from realestate.infrastructure.persistence.postgres.crawl_run_repo import (
        PostgresCrawlRunRepository,
    )

    config = load_site_config(SITES_DIR / "meeyland.yaml")
    await SyncSiteConfigsUseCase(PostgresConfigRepository(pg_pool)).execute([config])
    async with pg_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO crawl_runs (crawl_run_id, site_id, run_mode, status, started_at) VALUES "
            "('old_sweep', 'meeyland', 'full_sweep', 'completed', NOW() - INTERVAL '3 hours'), "
            "('stuck', 'meeyland', 'incremental', 'running', NOW() - INTERVAL '2 hours')"
        )
    repo = PostgresCrawlRunRepository(pg_pool)

    state = (await repo.get_schedule_states())["meeyland"]
    assert state.running and state.last_full_sweep_at is not None

    assert await repo.fail_interrupted_runs(max_age_minutes=60) == 1
    assert not (await repo.get_schedule_states())["meeyland"].running
