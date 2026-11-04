"""Scheduling policy: given a site's config and run history, should it crawl now, and how?"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from realestate.domain.entities.crawl_run import RunMode
from realestate.domain.entities.crawler_config import CrawlerConfig

# A scheduled full sweep may still start this long after its slot (scheduler restart, slow poll).
FULL_SWEEP_GRACE = timedelta(minutes=15)


@dataclass(frozen=True)
class SiteRunState:
    """What the scheduler knows about a site's past runs."""

    last_run_at: datetime | None = None
    last_full_sweep_at: datetime | None = None
    running: bool = False


@dataclass(frozen=True)
class ScheduleDecision:
    mode: RunMode
    slot: datetime | None = None  # the full-sweep slot being served, if any


def decide_run(
    config: CrawlerConfig,
    state: SiteRunState,
    now: datetime,
    *,
    grace: timedelta = FULL_SWEEP_GRACE,
) -> ScheduleDecision | None:
    """Return the run to start now, or None.

    * Full sweep: the latest slot of ``schedule_times`` (interpreted in ``source_timezone``)
      passed less than ``grace`` ago and no full sweep has started since that slot.
    * Incremental: ``interval_seconds`` elapsed since ``last_run_at`` (or the site never ran).
    * Nothing while a run for the site is in progress.
    """
    if not config.enabled or state.running:
        return None

    slot = config.latest_scheduled_slot(now)
    if slot is not None and timedelta(0) <= now - slot <= grace:
        already_swept = state.last_full_sweep_at is not None and state.last_full_sweep_at >= slot
        if not already_swept:
            return ScheduleDecision(RunMode.FULL_SWEEP, slot)

    interval = config.interval_seconds
    if interval:
        if state.last_run_at is None or (now - state.last_run_at).total_seconds() >= interval:
            return ScheduleDecision(RunMode.INCREMENTAL)
    return None
