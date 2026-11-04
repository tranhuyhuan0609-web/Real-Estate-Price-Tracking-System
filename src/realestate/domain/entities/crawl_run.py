from __future__ import annotations

from enum import Enum


class RunMode(str, Enum):
    INCREMENTAL = "incremental"
    FULL_SWEEP = "full_sweep"


class CrawlRunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
