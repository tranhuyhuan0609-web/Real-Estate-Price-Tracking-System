from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directory setup pointing to project root
BASE_DIR = Path(__file__).resolve().parents[3]


class BaseSubSettings(BaseSettings):
    """Base class for sub-settings sharing the same .env configuration."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        validate_default=True,
    )
