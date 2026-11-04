from __future__ import annotations

from typing import Any, Dict, List, Protocol, Tuple


class EventPublisher(Protocol):
    """Protocol for publishing events to a message bus like Kafka."""

    async def publish(self, topic: str, key: str, value: Dict[str, Any]) -> None: ...

    async def publish_batch(self, topic: str, events: List[Tuple[str, Dict[str, Any]]]) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
