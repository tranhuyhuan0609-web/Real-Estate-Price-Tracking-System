"""Interface for Bronze raw event repository."""

from __future__ import annotations

from typing import List, Protocol
from src.domain.entities import RawEventMessage


class BronzeRepository(Protocol):
    """Protocol for storing raw events into Bronze storage."""

    async def bulk_upsert_raw_events(self, events: List[RawEventMessage]) -> int:
        """Upsert a batch of raw events into storage asynchronously."""
        ...

