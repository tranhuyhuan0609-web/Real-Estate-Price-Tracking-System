"""Unit tests for scripts/replay_dlq.py."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from scripts.replay_dlq import replay_from_kafka, replay_from_mongo


@pytest.mark.asyncio
async def test_replay_from_mongo_success(monkeypatch):
    mock_collection = MagicMock()
    mock_docs = [
        {
            "_id": "doc_1",
            "raw_key": "mogi_101",
            "raw_payload": {"site_id": "mogi", "title": "Listing 1"},
            "error_type": "ValidationError",
        },
        {
            "_id": "doc_2",
            "raw_key": "meeyland_202",
            "raw_payload": {"site_id": "meeyland", "title": "Listing 2"},
            "error_type": "KeyError",
        },
    ]
    mock_collection.find.return_value.limit.return_value = mock_docs
    mock_collection.delete_many.return_value = MagicMock(deleted_count=2)

    mock_db = MagicMock()
    mock_db.__getitem__.return_value = mock_collection
    mock_client = MagicMock()
    mock_client.__getitem__.return_value = mock_db

    monkeypatch.setattr("scripts.replay_dlq.MongoClient", MagicMock(return_value=mock_client))

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
    assert mock_publisher.publish.call_count == 2
    mock_collection.delete_many.assert_called_once_with({"_id": {"$in": ["doc_1", "doc_2"]}})


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
    # First getmany returns records, second getmany returns empty (to break loop)
    mock_consumer.getmany = AsyncMock(side_effect=[{"tp": [mock_msg_1]}, {}])
    monkeypatch.setattr("scripts.replay_dlq.AIOKafkaConsumer", MagicMock(return_value=mock_consumer))

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
    mock_publisher.publish.assert_awaited_once_with(
        topic="realestate.raw_events",
        key="mogi_101",
        value={"site_id": "mogi", "data": "val"},
    )

