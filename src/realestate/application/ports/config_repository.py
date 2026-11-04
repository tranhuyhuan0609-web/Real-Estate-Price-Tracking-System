from __future__ import annotations

from typing import List, Protocol

from realestate.domain.entities.crawler_config import CrawlerConfig


class ConfigRepository(Protocol):
    """Protocol for managing site crawler configs."""

    async def get_config(self, site_id: str) -> CrawlerConfig: ...

    async def get_enabled_configs(self) -> List[CrawlerConfig]: ...

    async def create_config(self, config: CrawlerConfig) -> None: ...

    async def update_config(self, site_id: str, config: CrawlerConfig) -> None: ...

    async def delete_config(self, site_id: str) -> None: ...

    async def update_last_run(self, site_id: str) -> None: ...
