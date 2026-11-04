from unittest.mock import AsyncMock

import pytest

from realestate.entrypoints import cli


@pytest.mark.parametrize(
    "argv",
    [
        ["crawl", "--site", "mogi"],
        ["crawl", "--site", "mogi", "--mode", "full_sweep"],
        ["sync-configs", "--dry-run"],
        ["replay-dlq", "--source", "kafka", "--limit", "5"],
        ["reset", "--confirm"],
        ["reveal-phone", "--listing-key", "mogi_1"],
        ["telegram-chat-id"],
        ["telegram-test", "--message", "hi"],
    ],
)
def test_every_command_parses(argv):
    assert cli.build_parser().parse_args(argv).command == argv[0]


async def test_reset_without_confirm_touches_nothing(monkeypatch):
    container = AsyncMock()
    monkeypatch.setattr(cli, "AppContainer", container)
    await cli.run_reset(confirm=False)
    container.assert_not_called()


async def test_telegram_test_requires_chat_id(monkeypatch, capsys):
    from realestate.infrastructure.config.settings import load_settings

    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    load_settings.cache_clear()
    try:
        assert await cli.run_telegram_test("hi") == 1
        assert "telegram-chat-id" in capsys.readouterr().out
    finally:
        load_settings.cache_clear()


async def test_telegram_chat_id_lists_chats(monkeypatch, capsys):
    from realestate.infrastructure.config.settings import load_settings
    from realestate.infrastructure.notifications import telegram

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setattr(
        telegram.TelegramClient,
        "recent_chats",
        AsyncMock(return_value=[{"id": 777, "type": "private", "name": "An"}]),
    )
    load_settings.cache_clear()
    try:
        assert await cli.run_telegram_chat_id() == 0
        assert "777" in capsys.readouterr().out
    finally:
        load_settings.cache_clear()
