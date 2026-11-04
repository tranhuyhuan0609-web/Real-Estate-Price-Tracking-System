"""Minimal Telegram Bot API client (sendMessage / getUpdates) for alert setup and tests."""

from __future__ import annotations

from typing import Any

import httpx

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is empty. Create a bot with @BotFather first.")
        self._token = token
        self._timeout = timeout
        self._transport = transport

    async def _call(self, method: str, **params: Any) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            resp = await client.post(API.format(token=self._token, method=method), json=params)
        try:
            body = resp.json()
        except ValueError as exc:
            raise TelegramError(f"{method}: HTTP {resp.status_code}") from exc
        if not body.get("ok"):
            # Never echo the URL: it contains the bot token.
            raise TelegramError(f"{method}: {body.get('description', 'unknown error')}")
        return body["result"]

    async def send_message(self, chat_id: str, text: str) -> None:
        await self._call("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)

    async def recent_chats(self) -> list[dict[str, Any]]:
        """Chats that recently messaged the bot: [{'id', 'type', 'name'}], newest first."""
        updates = await self._call("getUpdates", timeout=0)
        chats: dict[int, dict[str, Any]] = {}
        for update in reversed(updates):
            message = (
                update.get("message")
                or update.get("channel_post")
                or update.get("my_chat_member")
                or {}
            )
            chat = message.get("chat")
            if chat and chat["id"] not in chats:
                name = (
                    chat.get("title")
                    or " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p)
                    or chat.get("username", "")
                )
                chats[chat["id"]] = {"id": chat["id"], "type": chat.get("type"), "name": name}
        return list(chats.values())
