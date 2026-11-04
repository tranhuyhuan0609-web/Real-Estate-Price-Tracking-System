"""Make the crawler_configs table mirror the declarative site configs (configs/sites/*.yaml)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from realestate.application.ports.config_repository import ConfigRepository
from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.domain.exceptions import ConfigNotFoundError

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)


class SyncSiteConfigsUseCase:
    """Upsert every given config. Site configs in the DB but not in the input are left alone."""

    def __init__(self, config_repo: ConfigRepository) -> None:
        self._repo = config_repo

    async def execute(self, configs: Iterable[CrawlerConfig], *, dry_run: bool = False) -> SyncReport:
        report = SyncReport()
        for config in configs:
            try:
                current = await self._repo.get_config(config.site_id)
            except ConfigNotFoundError:
                if not dry_run:
                    await self._repo.create_config(config)
                report.created.append(config.site_id)
                continue

            if current == config:
                report.unchanged.append(config.site_id)
                continue

            if not dry_run:
                await self._repo.update_config(config.site_id, config)
            report.updated.append(config.site_id)

        logger.info(
            "Site config sync%s: created=%s updated=%s unchanged=%d",
            " (dry run)" if dry_run else "",
            report.created,
            report.updated,
            len(report.unchanged),
        )
        return report
