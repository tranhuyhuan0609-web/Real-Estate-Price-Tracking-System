from pydantic import Field
from src.infrastructure.config.base import BaseSubSettings


class KafkaSettings(BaseSubSettings):
    """Kafka infrastructure configuration."""

    KAFKA_BOOTSTRAP_SERVERS: str = Field(default="localhost:9092", min_length=1)
    KAFKA_RAW_TOPIC: str = Field(default="realestate.raw_events", min_length=1)
    KAFKA_DLQ_TOPIC: str = Field(default="realestate.dlq_events", min_length=1)
    KAFKA_GROUP_BRONZE: str = Field(default="bds_bronze_mongo_group", min_length=1)
    KAFKA_GROUP_SILVER_GOLD: str = Field(default="bds_silver_gold_pg_group", min_length=1)
    KAFKA_GROUP_RAW_WRITER: str = Field(default="bds_bronze_mongo_group", min_length=1)
    KAFKA_GROUP_CLEAN_PROCESSOR: str = Field(default="bds_silver_gold_pg_group", min_length=1)
    KAFKA_GROUP_DLQ_WRITER: str = Field(default="bds_dlq_writer_group", min_length=1)
    KAFKA_DLQ_FLUSH_TIMEOUT: float = Field(default=10.0, gt=0, le=60)
