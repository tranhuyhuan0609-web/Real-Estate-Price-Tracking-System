from datetime import datetime, timedelta, timezone

from realestate.domain.entities import RunMode
from realestate.domain.services.scheduling import SiteRunState, decide_run
from realestate.infrastructure.persistence.postgres.config_mapping import row_to_config

UTC = timezone.utc


def _config(seed_row, **overrides):
    return row_to_config({**seed_row, **overrides})


def test_interval_sites(seed_row):
    cfg = _config(seed_row, schedule_times=[], interval_seconds=300)
    now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

    assert decide_run(cfg, SiteRunState(), now).mode is RunMode.INCREMENTAL  # never ran
    assert decide_run(cfg, SiteRunState(last_run_at=now - timedelta(seconds=50)), now) is None
    assert (
        decide_run(cfg, SiteRunState(last_run_at=now - timedelta(seconds=400)), now).mode
        is RunMode.INCREMENTAL
    )
    assert decide_run(cfg, SiteRunState(running=True), now) is None


def test_disabled_site_never_runs(seed_row):
    cfg = _config(seed_row, enabled=False)
    assert decide_run(cfg, SiteRunState(), datetime(2026, 11, 4, tzinfo=UTC)) is None


def test_full_sweep_within_grace_once_per_slot(seed_row):
    cfg = _config(seed_row, schedule_times=["05:00"], interval_seconds=None)
    slot = datetime(2026, 11, 4, 5, 0, tzinfo=UTC)

    decision = decide_run(cfg, SiteRunState(), slot + timedelta(minutes=3))
    assert decision.mode is RunMode.FULL_SWEEP and decision.slot == slot

    swept = SiteRunState(last_full_sweep_at=slot + timedelta(minutes=1))
    assert decide_run(cfg, swept, slot + timedelta(minutes=3)) is None  # already served
    assert decide_run(cfg, SiteRunState(), slot + timedelta(minutes=20)) is None  # grace passed


def test_schedule_times_use_source_timezone(seed_row):
    """05:00 in Asia/Ho_Chi_Minh is 22:00 UTC the day before — not 05:00 UTC."""
    cfg = _config(seed_row, schedule_times=["05:00"], interval_seconds=None)
    cfg = cfg.model_copy(update={"source_timezone": "Asia/Ho_Chi_Minh"})

    at_local_slot = datetime(2026, 11, 3, 22, 2, tzinfo=UTC)
    decision = decide_run(cfg, SiteRunState(), at_local_slot)
    assert decision is not None and decision.slot == datetime(2026, 11, 3, 22, 0, tzinfo=UTC)

    assert decide_run(cfg, SiteRunState(), datetime(2026, 11, 4, 5, 2, tzinfo=UTC)) is None


def test_full_sweep_wins_over_due_incremental(seed_row):
    cfg = _config(seed_row, schedule_times=["05:00"], interval_seconds=300)
    now = datetime(2026, 11, 4, 5, 1, tzinfo=UTC)
    assert (
        decide_run(cfg, SiteRunState(last_run_at=now - timedelta(hours=1)), now).mode
        is RunMode.FULL_SWEEP
    )


def test_content_timezone_does_not_move_the_schedule(seed_row):
    """Dates are read in Vietnam time by default, but schedule_times stay in source_timezone."""
    cfg = _config(seed_row, schedule_times=["05:00"], interval_seconds=None)
    assert cfg.content_timezone == "Asia/Ho_Chi_Minh" and cfg.source_timezone == "UTC"
    decision = decide_run(cfg, SiteRunState(), datetime(2026, 11, 4, 5, 1, tzinfo=UTC))
    assert decision is not None and decision.slot == datetime(2026, 11, 4, 5, 0, tzinfo=UTC)
