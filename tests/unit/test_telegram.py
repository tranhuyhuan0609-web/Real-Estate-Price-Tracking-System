import json

import httpx
import pytest

from realestate.infrastructure.notifications.telegram import TelegramClient, TelegramError


def _transport(responses: dict[str, dict], seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        seen.append((method, json.loads(request.content or b"{}")))
        return httpx.Response(200, json=responses[method])

    return httpx.MockTransport(handler)


async def test_send_message_posts_to_chat():
    seen: list = []
    client = TelegramClient(
        "123:abc", transport=_transport({"sendMessage": {"ok": True, "result": {}}}, seen)
    )
    await client.send_message("42", "hello")
    assert seen == [
        ("sendMessage", {"chat_id": "42", "text": "hello", "disable_web_page_preview": True})
    ]


async def test_recent_chats_newest_first_and_deduplicated():
    updates = [
        {"message": {"chat": {"id": 1, "type": "private", "first_name": "An"}}},
        {"message": {"chat": {"id": 2, "type": "group", "title": "Ops"}}},
        {"message": {"chat": {"id": 1, "type": "private", "first_name": "An"}}},
    ]
    client = TelegramClient(
        "t", transport=_transport({"getUpdates": {"ok": True, "result": updates}}, [])
    )
    assert await client.recent_chats() == [
        {"id": 1, "type": "private", "name": "An"},
        {"id": 2, "type": "group", "name": "Ops"},
    ]


async def test_api_error_does_not_leak_token():
    client = TelegramClient(
        "999:SECRET",
        transport=_transport({"sendMessage": {"ok": False, "description": "chat not found"}}, []),
    )
    with pytest.raises(TelegramError) as exc:
        await client.send_message("1", "x")
    assert "chat not found" in str(exc.value) and "SECRET" not in str(exc.value)


def test_empty_token_is_rejected():
    with pytest.raises(TelegramError, match="BotFather"):
        TelegramClient("")
