"""Prometheus metrics and liveness heartbeats shared by all processes.

Every process exposes /metrics on ``METRICS_PORT`` (when > 0). docker/monitoring/alerts.yml holds
the alert rules written against these names.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)

CONSUMER_MESSAGES = Counter(
    "realestate_consumer_messages_total",
    "Messages handled by a consumer, by outcome (stored | dlq | retry).",
    ["consumer", "outcome"],
)
CONSUMER_BATCH_SECONDS = Histogram(
    "realestate_consumer_batch_seconds",
    "Time to handle one polled batch.",
    ["consumer"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
CONSUMER_LAG = Gauge(
    "realestate_consumer_lag_messages",
    "Messages behind the partition high watermark.",
    ["consumer", "topic", "partition"],
)
HEARTBEAT = Gauge(
    "realestate_heartbeat_timestamp_seconds",
    "Last time the process main loop made progress (unix time).",
    ["process"],
)
CRAWL_RUNS = Counter(
    "realestate_crawl_runs_total",
    "Crawl sessions finished by the scheduler, by result: ok, failed (crawl_runs.status, e.g. "
    "health gate or 0 items) or error (exception).",
    ["site", "mode", "result"],
)
CRAWL_LAST_ITEMS = Gauge(
    "realestate_crawl_last_items",
    "Items scraped by the latest crawl session of a site and mode.",
    ["site", "mode"],
)
CRAWL_SECONDS = Histogram(
    "realestate_crawl_duration_seconds",
    "Wall time of one crawl session.",
    ["site", "mode"],
    buckets=(10, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200),
)
LIFECYCLE_EXPIRED = Counter(
    "realestate_lifecycle_expired_total",
    "Listings expired by lifecycle after a full sweep.",
    ["site"],
)


BACKUP_LAST_SUCCESS = Gauge(
    "realestate_backup_last_success_timestamp_seconds",
    "mtime of the newest complete dump per store (postgres | mongo).",
    ["store"],
)
BACKUP_STORES = ("postgres", "mongo")


def export_backup_ages(backup_dir: str) -> dict[str, float | None]:
    """Set BACKUP_LAST_SUCCESS from the newest finished (non-.tmp) dump of each store."""
    found: dict[str, float | None] = {}
    for store in BACKUP_STORES:
        files = [
            p for p in (Path(backup_dir) / store).glob("*") if p.is_file() and p.suffix != ".tmp"
        ]
        newest = max((p.stat().st_mtime for p in files), default=None)
        found[store] = newest
        # A store that never produced a dump reports 0, so BackupStale fires instead of staying silent.
        BACKUP_LAST_SUCCESS.labels(store).set(newest or 0)
    return found


def start_metrics_server(port: int) -> None:
    if port <= 0:
        return
    start_http_server(port)
    logger.info("Prometheus metrics on :%d/metrics", port)


class Heartbeat:
    """Marks main-loop progress: a gauge plus a file whose mtime the healthcheck reads."""

    def __init__(self, process: str, directory: str = "/tmp", min_interval: float = 5.0) -> None:
        self.process = process
        self.path = Path(directory) / f"realestate-{process}.heartbeat"
        self._min_interval = min_interval
        self._last = 0.0

    def beat(self) -> None:
        now = time.time()
        if now - self._last < self._min_interval:
            return
        self._last = now
        HEARTBEAT.labels(self.process).set(now)
        try:
            self.path.write_text(f"{now:.0f}\n")
        except OSError as exc:  # never let liveness bookkeeping break the worker
            logger.debug("Could not write heartbeat %s: %s", self.path, exc)


def heartbeat_age(process: str, directory: str = "/tmp") -> float | None:
    """Seconds since the last beat, or None if the process never beat."""
    path = Path(directory) / f"realestate-{process}.heartbeat"
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None
