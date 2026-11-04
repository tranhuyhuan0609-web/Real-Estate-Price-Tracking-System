from typing import Any, Literal

from pydantic import Field, SecretStr, model_validator

from realestate.infrastructure.config.base import BaseSubSettings


class KafkaSettings(BaseSubSettings):
    """Kafka infrastructure configuration."""

    KAFKA_BOOTSTRAP_SERVERS: str = Field(default="localhost:9092", min_length=1)
    KAFKA_RAW_TOPIC: str = Field(default="realestate.raw_events", min_length=1)
    KAFKA_DLQ_TOPIC: str = Field(default="realestate.dlq_events", min_length=1)
    KAFKA_GROUP_RAW_WRITER: str = Field(default="bds_bronze_mongo_group", min_length=1)
    KAFKA_GROUP_CLEAN_PROCESSOR: str = Field(default="bds_silver_gold_pg_group", min_length=1)
    KAFKA_GROUP_DLQ_WRITER: str = Field(default="bds_dlq_writer_group", min_length=1)

    # Transport security. PLAINTEXT is only acceptable on a private network (local compose).
    KAFKA_SECURITY_PROTOCOL: Literal["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"] = "PLAINTEXT"
    KAFKA_SASL_MECHANISM: Literal["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"] = "SCRAM-SHA-512"
    KAFKA_SASL_USERNAME: str = ""
    KAFKA_SASL_PASSWORD: SecretStr = Field(default=SecretStr(""))

    @model_validator(mode="after")
    def _sasl_needs_credentials(self) -> "KafkaSettings":
        if self.KAFKA_SECURITY_PROTOCOL.startswith("SASL") and not (
            self.KAFKA_SASL_USERNAME and self.KAFKA_SASL_PASSWORD.get_secret_value()
        ):
            raise ValueError("KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD are required for SASL.")
        return self

    def kafka_client_kwargs(self) -> dict[str, Any]:
        """Connection kwargs shared by every aiokafka producer, consumer and admin client."""
        kwargs: dict[str, Any] = {
            "bootstrap_servers": self.KAFKA_BOOTSTRAP_SERVERS,
            "security_protocol": self.KAFKA_SECURITY_PROTOCOL,
        }
        if self.KAFKA_SECURITY_PROTOCOL in ("SSL", "SASL_SSL"):
            from aiokafka.helpers import create_ssl_context

            kwargs["ssl_context"] = create_ssl_context()
        if self.KAFKA_SECURITY_PROTOCOL.startswith("SASL"):
            kwargs.update(
                sasl_mechanism=self.KAFKA_SASL_MECHANISM,
                sasl_plain_username=self.KAFKA_SASL_USERNAME,
                sasl_plain_password=self.KAFKA_SASL_PASSWORD.get_secret_value(),
            )
        return kwargs
