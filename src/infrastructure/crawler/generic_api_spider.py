"""Generic REST API spider handling pagination, rate limiting, domain allowlisting and response mapping."""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urlsplit
import httpx
from src.application.interfaces.spider import Spider
from src.domain.entities import ParsedListingItem, RunMode
from src.domain.entities.crawler_config import CrawlerConfig
from src.infrastructure.crawler.parser import ListingParser, extract_by_json_path

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
        self._proxy_index = 0

    async def _send_request_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        method: str,
        headers: Dict[str, str],
        params: Dict[str, Any],
        json_body: Optional[Dict[str, Any]],
    ) -> Optional[httpx.Response]:
        retries = self.rate_cfg.retry_times
        timeout = self.rate_cfg.timeout_sec

        proxies = (
            list(self.config.proxy_settings.proxies)
            if self.config.proxy_settings and self.config.proxy_settings.enabled and self.config.proxy_settings.proxies
            else []
        )

        for attempt in range(1 + retries):
            # Rotate proxy per retry if proxy list is configured
            current_proxy = None
            if proxies:
                current_proxy = proxies[(self._proxy_index + attempt) % len(proxies)]

            try:
                async with self.semaphore:
                    # If proxy rotation is requested per attempt
                    if current_proxy and current_proxy != getattr(client, "_current_proxy", None):
                        async with httpx.AsyncClient(proxy=current_proxy, headers=headers, follow_redirects=True) as rot_client:
                            response = await rot_client.request(
                                method=method,
                                url=url,
                                headers=headers,
                                params=params,
                                json=json_body,
                                timeout=timeout,
                            )
                    else:
                        response = await client.request(
                            method=method,
                            url=url,
                            headers=headers,
                            params=params,
                            json=json_body,
                            timeout=timeout,
                        )

                if response.status_code == 200:
                    return response
                elif response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "Attempt %d/%d for %s received status %d. Backing off...",
                        attempt + 1, retries + 1, url, response.status_code
                    )
                    if attempt < retries:
                        await asyncio.sleep(2.0 * (attempt + 1))
                else:
                    logger.error("HTTP error %d for URL: %s", response.status_code, url)
                    return None
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                logger.warning("Attempt %d/%d network error for %s: %s", attempt + 1, retries + 1, url, exc)
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
        start_page = self.page_cfg.start_page
        page_size = self.page_cfg.page_size
        download_delay = self.rate_cfg.download_delay_sec

        logger.info(
            "Starting crawl for site '%s' (mode: %s, max_pages: %d)",
            self.config.site_id, mode_str, max_pages
        )

        headers = dict(self.req_cfg.headers)
        if "User-Agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RealEstateBot/1.0"

        proxy_url = None
        if self.config.proxy_settings and self.config.proxy_settings.enabled and self.config.proxy_settings.proxies:
            proxy_url = self.config.proxy_settings.proxies[0]

        client_kwargs: dict[str, Any] = {"headers": headers, "follow_redirects": True}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        async with httpx.AsyncClient(**client_kwargs) as client:
            current_page = start_page

            while self.pages_crawled < max_pages:
                # Construct request params & payload for current page
                params = dict(self.req_cfg.query_params)
                body = copy.deepcopy(self.req_cfg.body_template) if self.req_cfg.body_template is not None else {}

                page_param_name = self.page_cfg.page_param or "page"
                limit_param_name = self.page_cfg.limit_param or "limit"

                if self.page_cfg.strategy == "offset_limit":
                    page_val = (current_page - start_page) * page_size
                else:
                    page_val = current_page

                if self.page_cfg.param_location == "body":
                    body[page_param_name] = page_val
                    if limit_param_name:
                        body[limit_param_name] = page_size
                else:
                    params[page_param_name] = page_val
                    if limit_param_name:
                        params[limit_param_name] = page_size

                request_body = body if self.req_cfg.method == "POST" else None

                response = await self._send_request_with_retry(
                    client=client,
                    url=str(self.req_cfg.url),
                    method=self.req_cfg.method,
                    headers=headers,
                    params=params,
                    json_body=request_body,
                )

                if response is None:
                    logger.warning("Empty response received. Stopping crawl at page %d", current_page)
                    self.aborted_reason = f"No usable HTTP response at page {current_page} (blocked or server error)"
                    break

                try:
                    payload = response.json()
                except Exception as exc:
                    logger.error("Failed to parse JSON response on page %d: %s", current_page, exc)
                    self.aborted_reason = f"Invalid JSON response at page {current_page}: {exc}"
                    break

                self.pages_crawled += 1

                # Stop path check (uses items_stop_path property)
                stop_path = self.config.items_stop_path
                stop_items = extract_by_json_path(payload, stop_path)
                if not stop_items or not isinstance(stop_items, list) or len(stop_items) == 0:
                    logger.info("No items found at stop path '%s' on page %d. Reached end of listings.", stop_path, current_page)
                    if current_page == start_page:
                        # An empty very first page is a layout change / block, not a real end of listings
                        self.aborted_reason = f"No items on first page (stop path '{stop_path}'); possible API change or bot block"
                    else:
                        self.exhausted = True
                    break

                # Extract items
                items_path = self.config.response_mapping.items_path
                items = extract_by_json_path(payload, items_path) if items_path != stop_path else stop_items

                if not items or not isinstance(items, list):
                    self.aborted_reason = f"Items path '{items_path}' missing or not a list at page {current_page}"
                    break

                for raw_item in items:
                    if not isinstance(raw_item, dict):
                        continue
                    try:
                        parsed_item = self.parser.parse_item(raw_item)

                        # Validate allowed domain if url is present
                        if parsed_item.details.url:
                            host = urlsplit(parsed_item.details.url).hostname
                            if host and not self.config.is_allowed_domain(host):
                                logger.warning("Listing host '%s' not allowed. Skipping item.", host)
                                continue

                        yield (parsed_item, raw_item)
                    except Exception as err:
                        logger.error("Error parsing listing item: %s", err)
                        continue

                current_page += 1
                if download_delay > 0:
                    await asyncio.sleep(download_delay)
