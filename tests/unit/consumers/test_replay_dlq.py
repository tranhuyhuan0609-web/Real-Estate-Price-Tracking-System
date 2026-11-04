"""Unit tests for dlq replay."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from realestate.infrastructure.messaging.dlq import replay_from_kafka, replay_from_mongo


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, _n):
        return self

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


@pytest.mark.asyncio
async def test_replay_from_mongo_success(monkeypatch):
    mock_docs = [
        {"_id": "doc_1", "raw_key": "mogi_101", "raw_payload": {"site_id": "mogi", "title": "Listing 1"}},
        {"_id": "doc_2", "raw_key": "mogi_202", "raw_payload": {"site_id": "mogi", "title": "Listing 2"}},
    ]
    mock_collection = MagicMock()
    mock_collection.find.return_value = _AsyncCursor(mock_docs)
    mock_collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
    mock_client = MagicMock()
    mock_client.__getitem__.return_value.__getitem__.return_value = mock_collection
    mock_client.close = AsyncMock()
    monkeypatch.setattr(
        "realestate.infrastructure.messaging.dlq.AsyncMongoClient", MagicMock(return_value=mock_client)
    )
    mock_publisher = AsyncMock()

    count = await replay_from_mongo(
        mongo_uri="mongodb://localhost:27017",
        db_name="test_db",
        target_topic="realestate.raw_events",
        publisher=mock_publisher,
        site_id="mogi",
        delete_on_replay=True,
    )

    assert count == 2
    assert mock_publisher.publish.await_count == 2
    mock_collection.delete_many.assert_awaited_once_with({"_id": {"$in": ["doc_1", "doc_2"]}})
    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_replay_from_kafka_success(monkeypatch):
    mock_msg_1 = MagicMock()
    mock_msg_1.offset = 10
    mock_msg_1.key = b"mogi_101"
    mock_msg_1.value = {
        "raw_key": "mogi_101",
        "raw_payload": {"site_id": "mogi", "data": "val"},
        "error_type": "ValidationError",
    }

    mock_consumer = AsyncMock()
    mock_consumer.getmany = AsyncMock(side_effect=[{"tp": [mock_msg_1]}, {}])
    monkeypatch.setattr("realestate.infrastructure.messaging.dlq.AIOKafkaConsumer", MagicMock(return_value=mock_consumer))

    mock_publisher = AsyncMock()

    count = await replay_from_kafka(
        bootstrap_servers="localhost:9092",
        dlq_topic="realestate.dlq_events",
        target_topic="realestate.raw_events",
        publisher=mock_publisher,
        site_id="mogi",
        limit=5,
    )

    assert count == 1
    assert mock_publisher.publish.call_count == 1
    mock_publisher.publish.assert_called_once_with(
        topic="realestate.raw_events",
        key="mogi_101",
        value={"site_id": "mogi", "data": "val"},
    )
