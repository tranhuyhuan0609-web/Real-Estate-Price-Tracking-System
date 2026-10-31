from typing import Optional
from urllib.parse import quote
from pydantic import Field, SecretStr
from src.infrastructure.config.base import BaseSubSettings


class DatabaseSettings(BaseSubSettings):
    """Database configuration for PostgreSQL and MongoDB."""

    # MongoDB (required by Bronze and DLQ services)
    MONGO_HOST: str = "127.0.0.1"
    MONGO_PORT: int = Field(default=27017, ge=1, le=65535)
    MONGO_USER: str = Field(default="")
    MONGO_PASSWORD: SecretStr = Field(default=SecretStr(""))
    MONGO_DB: str = Field(default="")

    # PostgreSQL (required by Scheduler, Ingestion, and Lifecycle services)
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = Field(default=5432, ge=1, le=65535)
    POSTGRES_USER: str = Field(default="")
    POSTGRES_PASSWORD: SecretStr = Field(default=SecretStr(""))
    POSTGRES_DB: str = Field(default="")

    @property
    def postgres_dsn(self) -> str:
        """Build a PostgreSQL connection string."""
        if not self.POSTGRES_USER or not self.POSTGRES_DB:
            raise ValueError("PostgreSQL settings are not configured (POSTGRES_USER, POSTGRES_DB required).")
        username = quote(self.POSTGRES_USER, safe="")
        password = quote(self.POSTGRES_PASSWORD.get_secret_value(), safe="")
        database = quote(self.POSTGRES_DB, safe="")
        return f"postgresql://{username}:{password}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{database}"

    @property
    def mongo_uri(self) -> str:
        """Build a MongoDB URI using the admin authentication database."""
        if not self.MONGO_USER or not self.MONGO_DB:
            raise ValueError("MongoDB settings are not configured (MONGO_USER, MONGO_DB required).")
        username = quote(self.MONGO_USER, safe="")
        password = quote(self.MONGO_PASSWORD.get_secret_value(), safe="")
        return f"mongodb://{username}:{password}@{self.MONGO_HOST}:{self.MONGO_PORT}/?authSource=admin"
