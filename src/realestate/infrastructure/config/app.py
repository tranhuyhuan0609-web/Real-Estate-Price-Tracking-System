from typing import Literal
from pydantic import Field
from src.infrastructure.config.base import BaseSubSettings


class AppSettings(BaseSubSettings):
    """General application and interval configurations."""

    # Application logging (Giữ lại ở đây để quản lý Log của Crawler)
    CRAWLER_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
