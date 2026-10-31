from functools import lru_cache
from pydantic import model_validator
from src.infrastructure.config.app import AppSettings
from src.infrastructure.config.database import DatabaseSettings
from src.infrastructure.config.kafka import KafkaSettings


class Settings(DatabaseSettings, KafkaSettings, AppSettings):
    """Main application settings aggregating all sub-configurations."""

    @model_validator(mode="after")
    def validate_configuration(self) -> "Settings":
        """Validate relationships between configuration values."""
        if self.KAFKA_GROUP_BRONZE == self.KAFKA_GROUP_SILVER_GOLD:
            raise ValueError(
                "Bronze and Silver/Gold consumers must use different consumer groups."
            )

        # The services actually deployed (docker-compose) use RAW_WRITER / CLEAN_PROCESSOR groups.
        # Sharing one group would split the topic between them so each layer misses events.
        if self.KAFKA_GROUP_RAW_WRITER == self.KAFKA_GROUP_CLEAN_PROCESSOR:
            raise ValueError(
                "Raw writer and clean processor consumers must use different consumer groups."
            )

        if self.KAFKA_RAW_TOPIC == self.KAFKA_DLQ_TOPIC:
            raise ValueError("The RAW topic and DLQ topic must be different.")

        return self


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return a cached application settings instance."""
    return Settings()
