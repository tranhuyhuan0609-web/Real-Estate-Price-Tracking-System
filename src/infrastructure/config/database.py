from urllib.parse import quote
from pydantic import Field, SecretStr
from src.infrastructure.config.base import BaseSubSettings


class DatabaseSettings(BaseSubSettings):
    """Database configuration for PostgreSQL and MongoDB."""

    # MongoDB
    MONGO_HOST: str = "127.0.0.1"
    MONGO_PORT: int = Field(default=27017, ge=1, le=65535)
    MONGO_USER: str
    MONGO_PASSWORD: SecretStr
    MONGO_DB: str

    # PostgreSQL
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = Field(default=5432, ge=1, le=65535)
    POSTGRES_USER: str
    POSTGRES_PASSWORD: SecretStr
    POSTGRES_DB: str

    @property
    def postgres_dsn(self) -> str:
        """Build a PostgreSQL connection string."""
        username = quote(self.POSTGRES_USER, safe="")
        password = quote(self.POSTGRES_PASSWORD.get_secret_value(), safe="")
        database = quote(self.POSTGRES_DB, safe="")
        return f"postgresql://{username}:{password}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{database}"

    @property
    def mongo_uri(self) -> str:
        """Build a MongoDB URI using the admin authentication database."""
        username = quote(self.MONGO_USER, safe="")
        password = quote(self.MONGO_PASSWORD.get_secret_value(), safe="")
        return f"mongodb://{username}:{password}@{self.MONGO_HOST}:{self.MONGO_PORT}/?authSource=admin"
