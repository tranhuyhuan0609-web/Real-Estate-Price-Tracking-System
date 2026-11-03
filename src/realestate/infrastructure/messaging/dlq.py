from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any
from realestate.infrastructure.messaging.producer import AioKafkaEventPublisher

logger = logging.getLogger(__name__)

async def forward_to_dlq(
    publisher: AioKafkaEventPublisher,
    dlq_topic: str,
    consumer_group: str,
    msg: Any,
    exc: Exception,
) -> bool:
    dlq_record = {
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "consumer_group": consumer_group,
        "topic": getattr(msg, "topic", "unknown"),
        "partition": getattr(msg, "partition", 0),
        "offset": getattr(msg, "offset", 0),
        "raw_key": msg.key.decode("utf-8", errors="replace") if isinstance(getattr(msg, "key", None), (bytes, bytearray)) else str(getattr(msg, "key", "")),
        "raw_payload": getattr(msg, "value", None),
        "error_type": exc.__class__.__name__,
        "error_message": str(exc),
    }
    try:
        await publisher.publish(
            topic=dlq_topic,
            key=dlq_record["raw_key"] or "unknown_key",
            value=dlq_record,
        )
        return True
    except Exception as err:
        logger.error("Failed to forward record to DLQ topic '%s': %s", dlq_topic, err)
        return False
