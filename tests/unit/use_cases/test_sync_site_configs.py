from realestate.application.use_cases.sync_site_configs import SyncSiteConfigsUseCase
from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.domain.exceptions import ConfigNotFoundError
from realestate.infrastructure.persistence.postgres.config_mapping import row_to_config


class InMemoryConfigRepo:
    def __init__(self, *configs: CrawlerConfig) -> None:
        self.data = {c.site_id: c for c in configs}
        self.writes: list[str] = []

    async def get_config(self, site_id: str) -> CrawlerConfig:
        try:
            return self.data[site_id]
        except KeyError:
            raise ConfigNotFoundError(site_id) from None

    async def create_config(self, config: CrawlerConfig) -> None:
        self.writes.append(f"create:{config.site_id}")
        self.data[config.site_id] = config

    async def update_config(self, site_id: str, config: CrawlerConfig) -> None:
        self.writes.append(f"update:{site_id}")
        self.data[site_id] = config


async def test_creates_updates_and_skips(seed_row):
    base = row_to_config(seed_row)
    changed = base.model_copy(update={"interval_seconds": 900})
    other = base.model_copy(update={"site_id": "other"})
    repo = InMemoryConfigRepo(base, other)

    new = base.model_copy(update={"site_id": "brand_new"})
    report = await SyncSiteConfigsUseCase(repo).execute([changed, other, new])

    assert report.created == ["brand_new"]
    assert report.updated == ["meeyland"]
    assert report.unchanged == ["other"]
    assert repo.data["meeyland"].interval_seconds == 900
    assert repo.writes == ["update:meeyland", "create:brand_new"]


async def test_dry_run_writes_nothing(seed_row):
    base = row_to_config(seed_row)
    repo = InMemoryConfigRepo()
    report = await SyncSiteConfigsUseCase(repo).execute([base], dry_run=True)
    assert report.created == ["meeyland"] and report.changed
    assert repo.writes == [] and repo.data == {}
