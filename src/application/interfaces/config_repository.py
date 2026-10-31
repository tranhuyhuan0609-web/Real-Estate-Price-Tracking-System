from abc import ABC, abstractmethod
from src.domain.entities.crawler_config import CrawlerConfig


class ConfigRepository(ABC):
    @abstractmethod
    async def get_config(self, site_id: str) -> CrawlerConfig:
        pass

    @abstractmethod
    async def get_enabled_configs(self) -> list[CrawlerConfig]:
        pass

    @abstractmethod
    async def create_config(self, config: CrawlerConfig) -> None:
        pass

    @abstractmethod
    async def update_config(self, site_id: str, config: CrawlerConfig) -> None:
        pass

    @abstractmethod
    async def delete_config(self, site_id: str) -> None:
        pass

    @abstractmethod
    async def update_last_run(self, site_id: str) -> None:
        pass
