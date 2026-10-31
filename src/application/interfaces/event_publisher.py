"""Event publisher interface for streaming raw crawler messages to message broker."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class EventPublisher(ABC):
    """Abstract interface for publishing events to a message bus like Kafka."""

    @abstractmethod
    async def publish(self, topic: str, key: str, value: Dict[str, Any]) -> None:
        """Publish a message with a partition key."""
        pass

    @abstractmethod
    async def publish_batch(self, topic: str, events: list[tuple[str, Dict[str, Any]]]) -> None:
        """Publish a batch of (key, value) messages."""
        pass

    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass

