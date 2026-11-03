from typing import Literal
from pydantic import Field
from realestate.infrastructure.config.base import BaseSubSettings


class AppSettings(BaseSubSettings):
    """General application and interval configurations."""

    CRAWLER_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
