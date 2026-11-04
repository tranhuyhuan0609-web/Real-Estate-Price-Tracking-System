"""Generic HTML spider handling pagination, rate limiting, CSS/XPath extraction via parsel, and optional detail page scraping."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx
from parsel import Selector

from realestate.application.ports.spider import Spider
from realestate.domain.entities import ParsedListingItem, RunMode
from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.infrastructure.crawler.http_clients import HttpClientPool
from realestate.infrastructure.crawler.parsing.listing_parser import (
    ListingParser,
    extract_by_json_path,
)

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
        # Crawl health signals read by CrawlSiteUseCase (see GenericApiSpider)
        self.aborted_reason: Optional[str] = None
        self.exhausted: bool = False
        self._clients: HttpClientPool | None = None

    def _extract_selector_value(
        self, context: Selector, selector_str: str, is_list: bool = False
    ) -> Any:
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

    def _extract_single_selector(
        self, context: Selector, selector_str: str, is_list: bool = False
    ) -> Any:
        """One selector: ``json:<sel> -> <path>``, ``::attr(x)`` / ``@x`` on the node itself,
        XPath (``/``, ``./``) or CSS. Text selectors join their non-empty tokens."""
        try:
            if selector_str.startswith("json:"):
                return self._embedded_json(context, selector_str)
            own_attr = _own_attribute(selector_str)
            if own_attr is not None:
                value = _clean(context.attrib.get(own_attr))
                return [value] if is_list and value else value

            is_xpath = selector_str.startswith(("/", "./"))
            res = context.xpath(selector_str) if is_xpath else context.css(selector_str)
            tokens = [t.strip() for t in res.getall() if t and t.strip()]
            if is_list:
                return tokens or None
            if "::text" in selector_str or "text()" in selector_str:
                return " ".join(tokens) or None

            value = res.get()
            if value is None and "::attr(" in selector_str:
                # The card itself may be the element carrying the attribute.
                value = context.attrib.get(selector_str.split("::attr(")[-1].rstrip(")"))
            return _clean(value)
        except Exception as exc:
            logger.debug("Selector '%s' extraction error: %s", selector_str, exc)
            return None

    def _embedded_json(self, context: Selector, selector_str: str) -> Any:
        """``json:<css_or_xpath> -> <json_path>``: read JSON from a script tag."""
        prefix, json_path = selector_str[5:].split("->", 1)
        script = self._extract_selector_value(context, prefix.strip())
        return extract_by_json_path(json.loads(script), json_path.strip()) if script else None

    async def _send_request(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[httpx.Response]:
        retries = self.rate_cfg.retry_times
        timeout = self.rate_cfg.timeout_sec
        assert self._clients is not None, "only called from crawl()"

        for attempt in range(1 + retries):
            client = self._clients.next()  # advances to the next proxy when rotating
            try:
                response = await client.get(url=url, params=params, timeout=timeout)

                if response.status_code == 200:
                    if self._clients is not None:
                        self._clients.record_success(client)
                    return response
                elif response.status_code in (429, 500, 502, 503, 504):
                    if self._clients is not None:
                        self._clients.record_failure(client)
                    logger.warning(
                        "Attempt %d/%d for %s status %d. Backing off...",
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

    def _build_page_url_and_params(
        self, current_page: int, current_offset: int
    ) -> Tuple[str, Dict[str, Any]]:
        """Construct the URL and query params based on pagination strategy."""
        base_url = (
            str(self.req_cfg.url)
            .replace("%7Bpage%7D", "{page}")
            .replace("%7Boffset%7D", "{offset}")
        )
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
        """Crawl list pages up to the page budget; enrich from detail pages; yield parsed items."""
        self.pages_crawled = 0
        self.aborted_reason = None
        self.exhausted = False
        mode_str = run_mode.value if isinstance(run_mode, RunMode) else run_mode
        max_pages = self.config.max_pages(mode_str)
        start_page = self.page_cfg.start_page
        logger.info(
            "Starting HTML crawl for site '%s' (mode: %s, max_pages: %d)",
            self.config.site_id,
            mode_str,
            max_pages,
        )
        detail_semaphore = asyncio.Semaphore(max(1, self.rate_cfg.concurrent_requests))

        async with HttpClientPool(
            self.config.proxy_settings, headers=self._headers(), follow_redirects=True
        ) as self._clients:
            current_page, current_offset = start_page, 0
            while self.pages_crawled < max_pages:
                url, params = self._build_page_url_and_params(current_page, current_offset)
                response = await self._send_request(url, params=params or None)
                if response is None:
                    logger.warning(
                        "No response for %s. Stopping crawl at page %d", url, current_page
                    )
                    self.aborted_reason = (
                        f"No usable HTTP response at page {current_page} (blocked or server error)"
                    )
                    break
                self.pages_crawled += 1

                raw_items = self._raw_items_from_page(response.text, str(response.url))
                if raw_items is None:  # no cards at all
                    if current_page == start_page:
                        # An empty first page is a layout change or a block, not the end.
                        self.aborted_reason = (
                            f"No items matched '{self.config.response_mapping.items_path}' on "
                            "first page; possible layout change or bot block"
                        )
                    else:
                        self.exhausted = True
                    break

                if self.detail_cfg.enabled:
                    await asyncio.gather(
                        *(self._enrich_from_detail(item, detail_semaphore) for item in raw_items)
                    )
                for raw_item in raw_items:
                    parsed = self._parse(raw_item)
                    if parsed is not None:
                        yield parsed, raw_item

                current_page += 1
                current_offset += self.page_cfg.page_size
                if self.rate_cfg.download_delay_sec > 0:
                    await asyncio.sleep(self.rate_cfg.download_delay_sec)

    # ------------------------------------------------------------------ crawl steps
    def _headers(self) -> Dict[str, str]:
        headers = dict(self.req_cfg.headers)
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36",
        )
        headers.setdefault(
            "Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        )
        return headers

    def _cards(self, selector: Selector) -> Tuple[List[Any], bool]:
        """Listing cards on a page, and whether they are embedded-JSON dicts."""
        items_path = self.config.response_mapping.items_path
        if items_path.startswith("json:"):
            prefix, json_path = items_path[5:].split("->", 1)
            script_val = self._extract_selector_value(selector, prefix.strip())
            if script_val:
                try:
                    found = extract_by_json_path(json.loads(script_val), json_path.strip())
                    if isinstance(found, list):
                        return found, True
                except Exception as json_err:
                    logger.error("Error parsing embedded JSON feed: %s", json_err)
            return [], True
        if items_path.startswith(("/", "./")):
            return list(selector.xpath(items_path)), False
        return list(selector.css(items_path)), False

    def _raw_items_from_page(self, html: str, page_url: str) -> Optional[List[Dict[str, Any]]]:
        """Raw field dicts of the cards that carry an id or URL; None when no card matched."""
        cards, is_json = self._cards(Selector(text=html))
        if not cards:
            logger.info(
                "No items matched selector '%s'. Reached end of listings.",
                self.config.response_mapping.items_path,
            )
            return None
        items = []
        for card in cards:
            raw: Dict[str, Any] = {}
            for field, path in self.config.response_mapping.fields.items():
                if is_json and isinstance(card, dict):
                    val = extract_by_json_path(card, path)
                else:
                    val = self._extract_selector_value(card, path, is_list=field == "media_list")
                if val is None:
                    continue
                if (
                    field in ("url", "thumbnail_url")
                    and isinstance(val, str)
                    and not val.startswith("http")
                ):
                    val = urljoin(page_url, val)
                raw[field] = val
            if raw.get("external_id") or raw.get("external_code") or raw.get("url"):
                items.append(raw)
        return items

    def _is_allowed_url(self, url: str) -> bool:
        host = urlsplit(url).hostname
        return not host or self.config.is_allowed_domain(host)

    async def _enrich_from_detail(self, item: Dict[str, Any], semaphore: asyncio.Semaphore) -> None:
        """Fill ``item`` with detail-page fields. Off-domain links (ads) are never fetched."""
        detail_url = item.get(self.detail_cfg.url_field) or item.get("url")
        if not detail_url or not self._is_allowed_url(detail_url):
            return
        async with semaphore:
            try:
                if self.rate_cfg.download_delay_sec > 0:
                    await asyncio.sleep(self.rate_cfg.download_delay_sec)
                response = await self._send_request(detail_url)
            except Exception as exc:
                logger.debug("Could not fetch detail page for %s: %s", detail_url, exc)
                return
        if not response or response.status_code != 200:
            return
        page = Selector(text=response.text)
        for field, path in self.detail_cfg.fields.items():
            is_list = field == "media_list"
            val = self._extract_selector_value(page, path, is_list=is_list)
            if val is None:
                continue
            item[field] = (
                [urljoin(detail_url, u) for u in val] if is_list and isinstance(val, list) else val
            )

    def _parse(self, raw_item: Dict[str, Any]) -> Optional[ParsedListingItem]:
        try:
            parsed = self.parser.parse_item(raw_item)
        except Exception as err:
            logger.error("Error parsing HTML listing item: %s", err)
            return None
        if parsed.details.url and not self._is_allowed_url(parsed.details.url):
            logger.warning(
                "Listing host '%s' not allowed. Skipping item.",
                urlsplit(parsed.details.url).hostname,
            )
            return None
        return parsed


def _own_attribute(selector: str) -> Optional[str]:
    """Attribute name for "::attr(name)" / "@name", which read the context node itself."""
    if selector.startswith("::attr(") and selector.endswith(")"):
        return selector[7:-1]
    if selector.startswith("@"):
        return selector[1:]
    return None


def _clean(value: Optional[str]) -> Optional[str]:
    return value.strip() if value and value.strip() else None
