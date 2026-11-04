from typing import Literal

from pydantic import Field, SecretStr

from realestate.infrastructure.config.base import BaseSubSettings


class AppSettings(BaseSubSettings):
    """General application configuration."""

    CRAWLER_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Prometheus /metrics port of each process; 0 disables the endpoint.
    METRICS_PORT: int = Field(default=0, ge=0, le=65535)
    # Directory of the liveness heartbeat files read by `realestate.entrypoints.healthcheck`.
    HEARTBEAT_DIR: str = "/tmp"
    # Where pg-backup / mongo-backup write dumps; the scheduler exports their age. "" = off.
    BACKUP_DIR: str = ""
    # Telegram alert delivery (Alertmanager + `realestate telegram-test`). Create a bot with
    # @BotFather, send it any message, then run `realestate telegram-chat-id`.
    TELEGRAM_BOT_TOKEN: SecretStr = Field(default=SecretStr(""))
    TELEGRAM_CHAT_ID: str = ""
