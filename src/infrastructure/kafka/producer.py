"""Asynchronous Kafka publisher implementation using aiokafka."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from aiokafka import AIOKafkaProducer
from src.application.interfaces.event_publisher import EventPublisher

logger = logging.getLogger(__name__)


class AioKafkaEventPublisher(EventPublisher):
    """Kafka event publisher implementation using aiokafka."""

    def __init__(self, bootstrap_servers: str) -> None:
        self.bootstrap_servers = bootstrap_servers
        self._producer: Optional[AIOKafkaProducer] = None

    async def start(self) -> None:
        if self._producer is None:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
            )
            await self._producer.start()
            logger.info("AIOKafkaProducer started on %s", self.bootstrap_servers)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            logger.info("AIOKafkaProducer stopped.")

    async def publish(self, topic: str, key: str, value: Dict[str, Any]) -> None:
        if self._producer is None:
            await self.start()
        assert self._producer is not None
        await self._producer.send_and_wait(topic=topic, key=key, value=value)

    async def publish_batch(self, topic: str, events: List[Tuple[str, Dict[str, Any]]]) -> None:
        """Publish a batch of (key, value) messages concurrently."""
        if not events:
            return
        if self._producer is None:
            await self.start()
        assert self._producer is not None
        tasks = [
            self._producer.send(topic=topic, key=k, value=v)
            for k, v in events
        ]
        await asyncio.gather(*tasks)

