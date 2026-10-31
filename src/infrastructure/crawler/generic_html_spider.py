"""Generic HTML spider handling pagination, rate limiting, CSS/XPath extraction via parsel, and optional detail page scraping."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit
import httpx
from parsel import Selector

from src.application.interfaces.spider import Spider
from src.domain.entities import ParsedListingItem, RunMode
from src.domain.entities.crawler_config import CrawlerConfig
from src.infrastructure.crawler.parser import ListingParser, extract_by_json_path

logger = logging.getLogger(__name__)


class GenericHtmlSpider(Spider):
    """Config-driven asynchronous spider for Server-Side Rendered (SSR) HTML real estate sources."""

    def __init__(self, config: CrawlerConfig) -> None:
        self.config = config
        self.parser = ListingParser(config)
        self.req_cfg = config.request
        self.page_cfg = config.pagination
        self.rate_cfg = config.rate_limit
        self.detail_cfg = config.detail_page
        self.pages_crawled: int = 0

    def _extract_selector_value(self, context: Selector, selector_str: str, is_list: bool = False) -> Any:
        """Extract text or attribute using CSS, XPath, or embedded JSON selector."""
        if not selector_str:
            return None
        selector_str = selector_str.strip()

        # Try comma-separated selectors in sequential priority order (fallback chain)
        if "," in selector_str and not selector_str.startswith("json:"):
            # Split by comma only when not inside brackets [] or parentheses () to preserve XPath/CSS functions
            parts = [p.strip() for p in re.split(r",(?![^(\[]*[)\]])", selector_str) if p.strip()]
            if len(parts) > 1:
                for part in parts:
                    val = self._extract_single_selector(context, part, is_list=is_list)
                    if val is not None and (not isinstance(val, (str, list)) or len(val) > 0):
                        return val
                return [] if is_list else None

        return self._extract_single_selector(context, selector_str, is_list=is_list)

    def _extract_single_selector(self, context: Selector, selector_str: str, is_list: bool = False) -> Any:
        try:
            # 0. Embedded JSON in script tag: "json:<css_or_xpath> -> <json_path>"
            if selector_str.startswith("json:"):
                prefix, json_path = selector_str[5:].split("->", 1)
                script_val = self._extract_selector_value(context, prefix.strip())
                if script_val:
                    data = json.loads(script_val)
                    return extract_by_json_path(data, json_path.strip())
                return None

            # 1. Attribute on context node itself: e.g. "::attr(data-id)" or "@data-id"
            if selector_str.startswith("::attr(") and selector_str.endswith(")"):
                attr_name = selector_str[7:-1]
                val = context.attrib.get(attr_name)
                val_clean = val.strip() if val and val.strip() else None
                return [val_clean] if is_list and val_clean else val_clean
            if selector_str.startswith("@"):
                attr_name = selector_str[1:]
                val = context.attrib.get(attr_name)
                val_clean = val.strip() if val and val.strip() else None
                return [val_clean] if is_list and val_clean else val_clean

            # 2. XPath or CSS extraction
            if selector_str.startswith("/") or selector_str.startswith("./"):
                res = context.xpath(selector_str)
            else:
                res = context.css(selector_str)

            if is_list:
                items = [s.strip() for s in res.getall() if s and s.strip()]
                return items if items else None

            # For text extraction (contains ::text or text()), join non-empty tokens
            if "::text" in selector_str or "text()" in selector_str:
                tokens = [s.strip() for s in res.getall() if s and s.strip()]
                return " ".join(tokens) if tokens else None

            val = res.get()
            if val is None and "::attr(" in selector_str:
                # Fallback if card itself matches the selector prefix
                attr_name = selector_str.split("::attr(")[-1].rstrip(")")
                val = context.attrib.get(attr_name)
            return val.strip() if val and val.strip() else None
        except Exception as exc:
            logger.debug("Selector '%s' extraction error: %s", selector_str, exc)
            return None

    async def _send_request(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[httpx.Response]:
        retries = self.rate_cfg.retry_times
        timeout = self.rate_cfg.timeout_sec

        for attempt in range(1 + retries):
            try:
                response = await client.get(url=url, params=params, timeout=timeout)
                if response.status_code == 200:
                    return response
                elif response.status_code in (429, 500, 502, 503, 504):
                    logger.warning("Attempt %d/%d for %s status %d. Backing off...", attempt + 1, retries + 1, url, response.status_code)
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

    def _build_page_url_and_params(self, current_page: int, current_offset: int) -> Tuple[str, Dict[str, Any]]:
        """Construct the URL and query params based on pagination strategy."""
        base_url = str(self.req_cfg.url).replace("%7Bpage%7D", "{page}").replace("%7Boffset%7D", "{offset}")
        params = dict(self.req_cfg.query_params)
        strategy = self.page_cfg.strategy
        page_param = self.page_cfg.page_param or "page"
        limit_param = self.page_cfg.limit_param or "limit"
        page_size = self.page_cfg.page_size

        if "{page}" in base_url or "{offset}" in base_url:
            url = base_url.format(page=current_page, offset=current_offset)
        elif strategy == "offset_limit":
            url = base_url
            params[page_param] = current_offset
            if limit_param:
                params[limit_param] = page_size
        else:
            url = base_url
            params[page_param] = current_page
            if limit_param:
                params[limit_param] = page_size

        return url, params

    async def crawl(
        self,
        run_mode: RunMode = RunMode.INCREMENTAL,
    ) -> AsyncGenerator[Tuple[ParsedListingItem, Dict[str, Any]], None]:
        """Crawl HTML pages and extract listing items according to CrawlerConfig."""
        self.pages_crawled = 0
        mode_str = run_mode.value if isinstance(run_mode, RunMode) else run_mode
        max_pages = self.config.max_pages(mode_str)
        start_page = self.page_cfg.start_page
        page_size = self.page_cfg.page_size
        download_delay = self.rate_cfg.download_delay_sec

        logger.info(
            "Starting HTML crawl for site '%s' (mode: %s, max_pages: %d)",
            self.config.site_id, mode_str, max_pages,
        )

        headers = dict(self.req_cfg.headers)
        if "User-Agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        if "Accept" not in headers:
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

        proxy_url = None
        if self.config.proxy_settings and self.config.proxy_settings.enabled and self.config.proxy_settings.proxies:
            proxy_url = self.config.proxy_settings.proxies[0]

        client_kwargs: Dict[str, Any] = {"headers": headers, "follow_redirects": True}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        concurrency = max(self.rate_cfg.concurrent_requests, 5)
        detail_semaphore = asyncio.Semaphore(concurrency)

        async with httpx.AsyncClient(**client_kwargs) as client:
            current_page = start_page
            current_offset = 0

            while self.pages_crawled < max_pages:
                url, params = self._build_page_url_and_params(current_page, current_offset)
                response = await self._send_request(client, url, params=params if params else None)

                if response is None:
                    logger.warning("No response received for %s. Stopping crawl at page %d", url, current_page)
                    break

                self.pages_crawled += 1
                selector = Selector(text=response.text)

                # 1. Locate listing items (either embedded JSON or HTML card nodes)
                items_path = self.config.response_mapping.items_path
                card_nodes: List[Any] = []
                is_json_feed = items_path.startswith("json:")

                if is_json_feed:
                    prefix, json_path = items_path[5:].split("->", 1)
                    script_val = self._extract_selector_value(selector, prefix.strip())
                    if script_val:
                        try:
                            data = json.loads(script_val)
                            items_list = extract_by_json_path(data, json_path.strip())
                            if isinstance(items_list, list):
                                card_nodes = items_list
                        except Exception as json_err:
                            logger.error("Error parsing embedded JSON feed: %s", json_err)
                elif items_path.startswith("/") or items_path.startswith("./"):
                    card_nodes = list(selector.xpath(items_path))
                else:
                    card_nodes = list(selector.css(items_path))

                if not card_nodes or len(card_nodes) == 0:
                    logger.info("No items matched selector '%s' on page %d. Reached end of listings.", items_path, current_page)
                    break

                page_raw_items: List[Dict[str, Any]] = []
                for card in card_nodes:
                    raw_item: Dict[str, Any] = {}

                    # Extract fields
                    if is_json_feed and isinstance(card, dict):
                        for field_name, field_path in self.config.response_mapping.fields.items():
                            val = extract_by_json_path(card, field_path)
                            if val is not None:
                                if field_name in ("url", "thumbnail_url") and isinstance(val, str) and not val.startswith("http"):
                                    val = urljoin(str(response.url), val)
                                raw_item[field_name] = val
                    else:
                        for field_name, sel in self.config.response_mapping.fields.items():
                            is_list = (field_name == "media_list")
                            val = self._extract_selector_value(card, sel, is_list=is_list)
                            if val is not None:
                                if field_name in ("url", "thumbnail_url") and isinstance(val, str) and not val.startswith("http"):
                                    val = urljoin(str(response.url), val)
                                raw_item[field_name] = val

                    if raw_item.get("external_id") or raw_item.get("external_code") or raw_item.get("url"):
                        page_raw_items.append(raw_item)

                # 2. Concurrently fetch deep detail pages with Semaphore
                if self.detail_cfg.enabled and page_raw_items:
                    async def _scrape_detail_worker(item_dict: Dict[str, Any]) -> None:
                        detail_url = item_dict.get("url")
                        if not detail_url:
                            return
                        async with detail_semaphore:
                            try:
                                if self.rate_cfg.download_delay_sec > 0:
                                    await asyncio.sleep(self.rate_cfg.download_delay_sec)
                                detail_res = await self._send_request(client, detail_url)
                                if detail_res and detail_res.status_code == 200:
                                    detail_sel = Selector(text=detail_res.text)
                                    for df_name, df_sel in self.detail_cfg.fields.items():
                                        is_list = (df_name == "media_list")
                                        val = self._extract_selector_value(detail_sel, df_sel, is_list=is_list)
                                        if val is not None:
                                            if is_list and isinstance(val, list):
                                                item_dict[df_name] = [urljoin(detail_url, u) for u in val]
                                            else:
                                                item_dict[df_name] = val
                            except Exception as d_err:
                                logger.debug("Could not fetch detail page for %s: %s", detail_url, d_err)

                    await asyncio.gather(*[_scrape_detail_worker(item) for item in page_raw_items])

                # 3. Parse and yield items
                for raw_item in page_raw_items:
                    try:
                        parsed_item = self.parser.parse_item(raw_item)

                        # Validate allowed domains
                        if parsed_item.details.url:
                            host = urlsplit(parsed_item.details.url).hostname
                            if host and not self.config.is_allowed_domain(host):
                                logger.warning("Listing host '%s' not allowed. Skipping item.", host)
                                continue

                        yield (parsed_item, raw_item)
                    except Exception as err:
                        logger.error("Error parsing HTML listing item: %s", err)
                        continue

                current_page += 1
                current_offset += page_size
                if download_delay > 0:
                    await asyncio.sleep(download_delay)
