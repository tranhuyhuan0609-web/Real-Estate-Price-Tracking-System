from realestate.bootstrap import AppContainer
from realestate.infrastructure.config.settings import Settings


def _settings() -> Settings:
    return Settings(
        POSTGRES_USER="u",
        POSTGRES_DB="d",
        MONGO_USER="m",
        MONGO_DB="raw",
        KAFKA_BOOTSTRAP_SERVERS="kafka:9092",
    )


async def test_container_is_lazy_and_closes_only_what_it_opened():
    c = AppContainer(_settings())
    assert c.sync_site_configs_uc is c.sync_site_configs_uc  # built once
    assert c.reveal_phone_uc is c.reveal_phone_uc
    assert c.phone_repo is c.phone_repo
    assert "pg_pool" in c.__dict__  # pool object exists ...
    assert not c._pg_opened  # ... but no connection was opened
    assert "bronze_repo" not in c.__dict__ and "publisher" not in c.__dict__
    await c.close()  # must not try to close the never-started pool/producer
