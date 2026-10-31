from pathlib import Path
import pytest
from pydantic import ValidationError
from src.infrastructure.config.base import BASE_DIR
from src.infrastructure.config.main import Settings, load_settings


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Fixture to automatically load a valid minimum set of env variables for every test."""
    monkeypatch.setenv("MONGO_HOST", "mongo")
    monkeypatch.setenv("MONGO_PORT", "27017")
    monkeypatch.setenv("MONGO_USER", "huyhuan")
    monkeypatch.setenv("MONGO_PASSWORD", "16102007")
    monkeypatch.setenv("MONGO_DB", "Raw_data")

    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "huyhuan")
    monkeypatch.setenv("POSTGRES_PASSWORD", "16102007")
    monkeypatch.setenv("POSTGRES_DB", "Realestate_data")

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:19092")
    monkeypatch.setenv("KAFKA_RAW_TOPIC", "realestate.raw_events")
    monkeypatch.setenv("KAFKA_DLQ_TOPIC", "realestate.dlq_events")
    monkeypatch.setenv("KAFKA_GROUP_RAW_WRITER", "bds_bronze_mongo_group")
    monkeypatch.setenv("KAFKA_GROUP_CLEAN_PROCESSOR", "bds_silver_gold_pg_group")
    monkeypatch.setenv("CRAWLER_LOG_LEVEL", "INFO")


def test_base_dir_resolves_to_project_root():
    """Ensure BASE_DIR correctly points to the directory containing .env and docker-compose.yml."""
    assert (BASE_DIR / ".env").exists()
    assert (BASE_DIR / "docker-compose.yml").exists()


def test_settings_load_successfully():
    """Test loading configuration successfully across all grouped modules."""
    settings = Settings()

    assert settings.MONGO_PORT == 27017
    assert settings.POSTGRES_USER == "huyhuan"
    assert settings.KAFKA_RAW_TOPIC == "realestate.raw_events"
    assert settings.KAFKA_GROUP_RAW_WRITER == "bds_bronze_mongo_group"
    assert settings.CRAWLER_LOG_LEVEL == "INFO"


def test_postgres_and_mongo_dsn_generation(monkeypatch):
    """Test that DSN properties correctly escape special characters."""
    monkeypatch.setenv("POSTGRES_USER", "user@test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pass/word")
    monkeypatch.setenv("POSTGRES_DB", "db_name")

    settings = Settings()
    assert "user%40test:pass%2Fword@postgres:5432/db_name" in settings.postgres_dsn
    assert "authSource=admin" in settings.mongo_uri


def test_duplicate_consumer_groups_raise_error(monkeypatch):
    """Test that identical bronze and silver/gold consumer groups are disallowed."""
    monkeypatch.setenv("KAFKA_GROUP_RAW_WRITER", "same_group")
    monkeypatch.setenv("KAFKA_GROUP_CLEAN_PROCESSOR", "same_group")

    with pytest.raises(ValidationError, match="must use different consumer groups"):
        Settings()


def test_duplicate_kafka_topics_raise_error(monkeypatch):
    """Test that raw and dlq topic cannot be identical."""
    monkeypatch.setenv("KAFKA_RAW_TOPIC", "same_topic")
    monkeypatch.setenv("KAFKA_DLQ_TOPIC", "same_topic")

    with pytest.raises(ValidationError, match="The RAW topic and DLQ topic must be different"):
        Settings()
