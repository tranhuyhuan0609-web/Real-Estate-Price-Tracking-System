"""Generic REST API spider handling pagination, rate limiting, domain allowlisting and response mapping."""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from realestate.application.ports.spider import Spider
from realestate.domain.entities import ParsedListingItem, RunMode
from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.infrastructure.crawler.http_clients import HttpClientPool
from realestate.infrastructure.crawler.parsing.listing_parser import (
    ListingParser,
    extract_by_json_path,
)

logger = logging.getLogger(__name__)


class GenericApiSpider(Spider):
    """Config-driven asynchronous spider for REST API real estate sources."""

    def __init__(self, config: CrawlerConfig) -> None:
        self.config = config
        self.parser = ListingParser(config)
        self.req_cfg = config.request
        self.page_cfg = config.pagination
        self.rate_cfg = config.rate_limit
        self.pages_crawled: int = 0
        # Crawl health signals read by CrawlSiteUseCase:
        #   aborted_reason - set when the crawl stopped because of an error (not end of listings)
        #   exhausted      - True only when the end of the listings was actually reached
        self.aborted_reason: Optional[str] = None
        self.exhausted: bool = False
        self.semaphore = asyncio.Semaphore(self.rate_cfg.concurrent_requests)
        self._clients: HttpClientPool | None = None

    async def _send_request_with_retry(
        self,
        url: str,
        method: str,
        headers: Dict[str, str],
        params: Dict[str, Any],
        json_body: Optional[Dict[str, Any]],
    ) -> Optional[httpx.Response]:
        retries = self.rate_cfg.retry_times
        timeout = self.rate_cfg.timeout_sec

        assert self._clients is not None, "only called from crawl()"

        for attempt in range(1 + retries):
            client = self._clients.next()  # advances to the next proxy when rotating
            try:
                async with self.semaphore:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        timeout=timeout,
                    )

                if response.status_code == 200:
                    if self._clients is not None:
                        self._clients.record_success(client)
                    return response
                elif response.status_code in (429, 500, 502, 503, 504):
                    if self._clients is not None:
                        self._clients.record_failure(client)
                    logger.warning(
                        "Attempt %d/%d for %s received status %d. Backing off...",
                        attempt + 1,
                        retries + 1,
                        url,
                        response.status_code,
                    )
                    if attempt < retries:
                        await asyncio.sleep(2.0 * (attempt + 1))
                else:
                    logger.error("HTTP error %d for URL: %s", response.status_code, url)
                    return None
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                if self._clients is not None:
                    self._clients.record_failure(client)
                logger.warning(
                    "Attempt %d/%d network error for %s: %s", attempt + 1, retries + 1, url, exc
                )
                if attempt == retries:
                    raise
                await asyncio.sleep(2.0 * (attempt + 1))

        return None

    async def crawl(
        self,
        run_mode: RunMode = RunMode.INCREMENTAL,
    ) -> AsyncGenerator[Tuple[ParsedListingItem, Dict[str, Any]], None]:
        """Crawl pages up to the page budget for the specified run mode."""
        self.pages_crawled = 0
        self.aborted_reason = None
        self.exhausted = False
        mode_str = run_mode.value if isinstance(run_mode, RunMode) else run_mode
        max_pages = self.config.max_pages(mode_str)
        logger.info(
            "Starting crawl for site '%s' (mode: %s, max_pages: %d)",
            self.config.site_id,
            mode_str,
            max_pages,
        )
        headers = dict(self.req_cfg.headers)
        headers.setdefault(
            "User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RealEstateBot/1.0"
        )

        async with HttpClientPool(
            self.config.proxy_settings, headers=headers, follow_redirects=True
        ) as self._clients:
            current_page = self.page_cfg.start_page
            while self.pages_crawled < max_pages:
                params, body = self._page_request(current_page)
                response = await self._send_request_with_retry(
                    url=str(self.req_cfg.url),
                    method=self.req_cfg.method,
                    headers=headers,
                    params=params,
                    json_body=body,
                )
                if response is None:
                    logger.warning(
                        "Empty response received. Stopping crawl at page %d", current_page
                    )
                    self.aborted_reason = (
                        f"No usable HTTP response at page {current_page} (blocked or server error)"
                    )
                    break
                try:
                    payload = response.json()
                except Exception as exc:
                    logger.error("Failed to parse JSON response on page %d: %s", current_page, exc)
                    self.aborted_reason = f"Invalid JSON response at page {current_page}: {exc}"
                    break
                self.pages_crawled += 1

                items = self._page_items(payload, current_page)
                if items is None:
                    break
                for raw_item in items:
                    parsed = self._parse(raw_item) if isinstance(raw_item, dict) else None
                    if parsed is not None:
                        yield parsed, raw_item

                current_page += 1
                if self.rate_cfg.download_delay_sec > 0:
                    await asyncio.sleep(self.rate_cfg.download_delay_sec)

    # ------------------------------------------------------------------ crawl steps
    def _page_request(self, page: int) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Query params and JSON body (POST only) for one page."""
        params = dict(self.req_cfg.query_params)
        body = (
            copy.deepcopy(self.req_cfg.body_template)
            if self.req_cfg.body_template is not None
            else {}
        )
        page_size = self.page_cfg.page_size
        if self.page_cfg.strategy == "offset_limit":
            value = (page - self.page_cfg.start_page) * page_size
        else:
            value = page
        target = body if self.page_cfg.param_location == "body" else params
        target[self.page_cfg.page_param or "page"] = value
        limit_param = self.page_cfg.limit_param or "limit"
        if limit_param:
            target[limit_param] = page_size
        return params, body if self.req_cfg.method == "POST" else None

    def _page_items(self, payload: Any, page: int) -> Optional[List[Any]]:
        """Items of a page, or None to stop: end of listings (exhausted) or a broken page (aborted)."""
        stop_path = self.config.items_stop_path
        stop_items = extract_by_json_path(payload, stop_path)
        if not stop_items or not isinstance(stop_items, list):
            logger.info(
                "No items found at stop path '%s' on page %d. Reached end of listings.",
                stop_path,
                page,
            )
            if page == self.page_cfg.start_page:
                # An empty first page is an API change or a block, not the end of listings.
                self.aborted_reason = f"No items on first page (stop path '{stop_path}'); possible API change or bot block"
            else:
                self.exhausted = True
            return None
        items_path = self.config.response_mapping.items_path
        items = stop_items if items_path == stop_path else extract_by_json_path(payload, items_path)
        if not items or not isinstance(items, list):
            self.aborted_reason = f"Items path '{items_path}' missing or not a list at page {page}"
            return None
        return items

    def _parse(self, raw_item: Dict[str, Any]) -> Optional[ParsedListingItem]:
        try:
            parsed = self.parser.parse_item(raw_item)
        except Exception as err:
            logger.error("Error parsing listing item: %s", err)
            return None
        host = urlsplit(parsed.details.url).hostname if parsed.details.url else None
        if host and not self.config.is_allowed_domain(host):
            logger.warning("Listing host '%s' not allowed. Skipping item.", host)
            return None
        return parsed
