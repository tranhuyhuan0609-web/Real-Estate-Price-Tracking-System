from typing import Literal

from realestate.infrastructure.config.base import BaseSubSettings


class AppSettings(BaseSubSettings):
    """General application configuration."""

    CRAWLER_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
