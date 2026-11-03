from __future__ import annotations
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class BaseConsumer:
    def __init__(self) -> None:
        self.shutdown_event = asyncio.Event()

    def stop(self) -> None:
        self.shutdown_event.set()
