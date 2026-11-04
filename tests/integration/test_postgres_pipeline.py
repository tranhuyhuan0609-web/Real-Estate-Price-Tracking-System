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
            listing_key=parsed.listing_key,
            site_id="meeyland",
            crawl_run_id="run_1",
            scraped_at=at,
            parsed_data=parsed,
            raw_payload=raw,
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
            listing_key=parsed.listing_key,
            site_id="meeyland",
            crawl_run_id="run_1",
            scraped_at=at,
            parsed_data=parsed,
            raw_payload=raw,
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


async def test_detail_update_detects_media_only_change(pg_pool):
    """A re-crawl where only the photos changed must still update listing_details."""
    config = load_site_config(SITES_DIR / "meeyland.yaml")
    await SyncSiteConfigsUseCase(PostgresConfigRepository(pg_pool)).execute([config])
    await _seed_run(pg_pool, "run_1")
    raw = load_fixture("meeyland_item.json")
    item = ListingParser(config).parse_item(raw)
    uc = ProcessSilverGoldUseCase(pg_repo=PgListingRepository(pg_pool))
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def event(parsed, at):
        return RawEventMessage(
            listing_key=parsed.listing_key,
            site_id="meeyland",
            crawl_run_id="run_1",
            scraped_at=at,
            parsed_data=parsed,
            raw_payload=raw,
        )

    async def stored():
        async with pg_pool.connection() as conn:
            cur = await conn.execute(
                "SELECT media_list, updated_at FROM listing_details WHERE listing_key = %s",
                (item.listing_key,),
            )
            return await cur.fetchone()

    await uc.execute(event(item, t0))
    _, first_update = await stored()

    await uc.execute(event(item, t0 + timedelta(hours=1)))  # identical -> no write
    assert (await stored())[1] == first_update

    new_media = ["https://img.example.com/new-1.jpg", "https://img.example.com/new-2.jpg"]
    changed = item.model_copy(
        update={"details": item.details.model_copy(update={"media_list": new_media})}
    )
    await uc.execute(event(changed, t0 + timedelta(hours=2)))
    media, _ = await stored()
    assert media == new_media


async def test_batch_keeps_input_order_and_uses_event_time(pg_pool):
    from realestate.application.ports.listing_repository import ListingObservation

    config = load_site_config(SITES_DIR / "meeyland.yaml")
    await SyncSiteConfigsUseCase(PostgresConfigRepository(pg_pool)).execute([config])
    await _seed_run(pg_pool, "run_1")
    item = ListingParser(config).parse_item(load_fixture("meeyland_item.json"))
    other = item.model_copy(update={"listing_key": "meeyland_000000001"})
    newer = datetime(2026, 11, 2, tzinfo=timezone.utc)
    older = newer - timedelta(days=1)
    cheaper = item.model_copy(update={"current_price_vnd": 1_000_000_000})

    results = await PgListingRepository(pg_pool).ingest_batch_transactional(
        [
            ListingObservation(item, "run_1", newer),
            ListingObservation(other, "run_1", newer),
            ListingObservation(cheaper, "run_1", older),  # stale within the same batch
        ]
    )

    assert results == [(True, False), (True, False), (False, False)]
    async with pg_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT current_price_vnd, first_seen_at FROM listings WHERE listing_key = %s",
            (item.listing_key,),
        )
        assert await cur.fetchone() == (item.current_price_vnd, older)


async def test_feeds_of_one_portal_share_listings_and_scope_lifecycle(pg_pool):
    """ADR 0005: mogi + mogi_apartment see one ad once; each feed's sweep only ages its own ads."""
    from realestate.application.ports.listing_repository import ListingObservation
    from realestate.infrastructure.persistence.postgres.lifecycle_repo import PgLifecycleRepository

    mogi = load_site_config(SITES_DIR / "mogi.yaml")
    mogi_apt = load_site_config(SITES_DIR / "mogi_apartment.yaml")
    meeyland = load_site_config(SITES_DIR / "meeyland.yaml")
    await SyncSiteConfigsUseCase(PostgresConfigRepository(pg_pool)).execute(
        [mogi, mogi_apt, meeyland]
    )
    await _seed_run(pg_pool, "run_1")
    base = ListingParser(load_site_config(SITES_DIR / "meeyland.yaml")).parse_item(
        load_fixture("meeyland_item.json")
    )
    apartment = base.model_copy(update={"listing_key": "mogi_100", "source": "mogi"})
    house = base.model_copy(update={"listing_key": "mogi_200", "source": "mogi"})
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    repo = PgListingRepository(pg_pool)

    results = await repo.ingest_batch_transactional(
        [
            ListingObservation(apartment, "run_1", t0, feed="mogi"),
            ListingObservation(apartment, "run_1", t0, feed="mogi_apartment"),
            ListingObservation(house, "run_1", t0, feed="mogi"),
        ]
    )
    assert results[0][0] and not results[1][0]  # second feed did not create a second listing

    sweep_start = t0 + timedelta(days=1)  # mogi_apartment sweep that found nothing
    stats = await PgLifecycleRepository(pg_pool).apply_lifecycle_transaction(
        site_id="mogi_apartment", started_at=sweep_start, crawl_run_id="run_1"
    )
    assert stats["incremented"] == 1

    async with pg_pool.connection() as conn:
        cur = await conn.execute("SELECT listing_key, missing_count FROM listings ORDER BY 1")
        assert await cur.fetchall() == [("mogi_100", 1), ("mogi_200", 0)]
        cur = await conn.execute(
            "SELECT count(*) FROM listing_feeds WHERE listing_key = 'mogi_100'"
        )
        assert (await cur.fetchone())[0] == 2


async def test_history_partitions_are_created_ahead_and_drain_default(pg_pool):
    from realestate.infrastructure.persistence.postgres.lifecycle_repo import PgLifecycleRepository

    config = load_site_config(SITES_DIR / "meeyland.yaml")
    await SyncSiteConfigsUseCase(PostgresConfigRepository(pg_pool)).execute([config])
    item = ListingParser(config).parse_item(load_fixture("meeyland_item.json"))
    await _seed_run(pg_pool, "run_1")
    await PgListingRepository(pg_pool).ingest_listing_transactional(
        item, "run_1", update_crawl_run=False
    )

    far = datetime.now(timezone.utc).replace(day=1) + timedelta(days=200)  # beyond months_ahead=2
    async with pg_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO listing_price_history (listing_key, price_vnd, price_status, recorded_at) "
            "VALUES (%s, 1, 'initial', %s)",
            (item.listing_key, far),
        )
        cur = await conn.execute(
            "SELECT tableoid::regclass::text FROM listing_price_history WHERE recorded_at = %s",
            (far,),
        )
        assert (await cur.fetchone())[0] == "listing_price_history_default"

    created = await PgLifecycleRepository(pg_pool).ensure_history_partitions(months_ahead=8)
    assert created >= 1

    async with pg_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT tableoid::regclass::text FROM listing_price_history WHERE recorded_at = %s",
            (far,),
        )
        assert (await cur.fetchone())[0] == f"listing_price_history_y{far:%Y}m{far:%m}"
        cur = await conn.execute("SELECT count(*) FROM listing_price_history_default")
        assert (await cur.fetchone())[0] == 0
    assert await PgLifecycleRepository(pg_pool).ensure_history_partitions(months_ahead=8) == 0


async def test_only_one_scheduler_leads_and_standby_takes_over(postgres_dsn):
    from realestate.infrastructure.persistence.postgres.leader_lock import PgAdvisoryLeaderLock

    a, b = (
        PgAdvisoryLeaderLock(postgres_dsn, key=4242),
        PgAdvisoryLeaderLock(postgres_dsn, key=4242),
    )
    try:
        assert await a.ensure() is True
        assert await b.ensure() is False  # standby
        assert await a.ensure() is True  # leader keeps it across polls

        await a.close()  # leader dies -> Postgres releases the session lock
        assert await b.ensure() is True
        assert await a.ensure() is False
    finally:
        await a.close()
        await b.close()
