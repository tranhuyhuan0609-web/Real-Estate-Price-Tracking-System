"""On-demand phone number extraction and reveal service."""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

import httpx
from parsel import Selector

from realestate.infrastructure.crawler.parsing.listing_parser import (
    clean_email,
    decode_cloudflare_email,
    extract_email_from_text,
)

logger = logging.getLogger(__name__)

# Standard Vietnamese mobile phone pattern (10 digits starting with 03, 05, 07, 08, 09)
VN_PHONE_REGEX = re.compile(r"(?:(?:\+84|84|0)(3[2-9]|5[2689]|7[06-9]|8[1-9]|9\d)\d{7})")

# Known button/trigger selectors for click-to-reveal on real estate sites
REVEAL_BUTTON_SELECTORS = [
    "button:has-text('Hiện số')",
    "button:has-text('Bấm để hiện số')",
    "button:has-text('Xem số điện thoại')",
    "button:has-text('Nhấn để xem')",
    ".btn-phone",
    ".btn-show-phone",
    ".phone-reveal-btn",
    "a.phone-member",
    "#detailTel",
    "[data-action='show-phone']",
]


def clean_phone_number(raw_phone: Optional[str]) -> Optional[str]:
    """Normalize a raw phone string to standardized 10-digit format (0xxxxxxxxx)."""
    if not raw_phone:
        return None
    # Remove whitespace, dots, hyphens, brackets
    cleaned = re.sub(r"[\s\.\-\(\)]", "", str(raw_phone).strip())
    # Convert +84 or 84 prefix to 0
    if cleaned.startswith("+84"):
        cleaned = "0" + cleaned[3:]
    elif cleaned.startswith("84") and len(cleaned) == 11:
        cleaned = "0" + cleaned[2:]

    # Check if masked
    if "*" in cleaned or "x" in cleaned.lower():
        return None

    match = VN_PHONE_REGEX.search(cleaned)
    if match:
        p = match.group(0)
        if p.startswith("+84"):
            p = "0" + p[3:]
        elif p.startswith("84") and len(p) == 11:
            p = "0" + p[2:]
        return p
    return None


class PhoneRevealer:
    """Service to reveal and extract full unmasked advertiser phone numbers on demand."""

    def __init__(self, timeout_sec: float = 10.0) -> None:
        self.timeout_sec = timeout_sec
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    async def reveal_phone(
        self,
        url: str,
        source: str = "",
        existing_phone: Optional[str] = None,
    ) -> Optional[str]:
        """Reveal the full phone number of a listing.

        1. ``existing_phone`` if it is already unmasked.
        2. Static HTML: data attributes, tel:/Zalo links, hidden inputs, inline JSON.
        3. Click-to-reveal in a headless browser (only if Playwright is installed).
        """
        phone, _ = await self.reveal_contact(
            url, source, existing_phone=existing_phone, want_email=False
        )
        return phone

    async def reveal_email(
        self,
        url: str,
        source: str = "",
        existing_email: Optional[str] = None,
    ) -> Optional[str]:
        """Reveal the unmasked advertiser email (existing value, then static HTML)."""
        _, email = await self.reveal_contact(
            url, source, existing_email=existing_email, want_phone=False
        )
        return email

    async def reveal_contact(
        self,
        url: str,
        source: str = "",
        existing_phone: Optional[str] = None,
        existing_email: Optional[str] = None,
        *,
        want_phone: bool = True,
        want_email: bool = True,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Reveal phone and email, downloading the listing page at most once."""
        phone = clean_phone_number(existing_phone) if want_phone else None
        email = clean_email(existing_email) if want_email else None
        need_phone = want_phone and phone is None
        need_email = want_email and email is None
        if not (need_phone or need_email):
            return phone, email
        if not url:
            logger.warning("Cannot reveal contact for source '%s': URL is empty.", source)
            return phone, email

        html = await self._fetch_html(url)
        if html:
            if need_phone:
                phone = self.extract_from_html(html)
            if need_email:
                email = self.extract_email_from_html(html)

        if want_phone and phone is None:
            phone = await self._extract_via_headless(url)
        return phone, email

    async def _fetch_html(self, url: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient(
                headers=self.headers, timeout=self.timeout_sec, follow_redirects=True
            ) as client:
                resp = await client.get(url)
        except Exception as exc:
            logger.debug("Failed HTTP GET %s: %s", url, exc)
            return None
        if resp.status_code >= 400:
            logger.debug("HTTP GET %s returned %s", url, resp.status_code)
            return None
        return resp.text

    def extract_from_html(self, html: str) -> Optional[str]:
        """Extract phone from HTML string using multiple heuristics."""
        if not html:
            return None

        sel = Selector(text=html)

        # 1. Check data-phone / data-tel attributes
        for attr in [
            "data-phone",
            "data-tel",
            "data-full-phone",
            "data-mobile",
            "data-contact-phone",
            "data-raw-phone",
        ]:
            val = sel.css(f"[{attr}]::attr({attr})").get()
            cleaned = clean_phone_number(val)
            if cleaned:
                logger.info("Found phone via attribute [%s]: %s", attr, cleaned)
                return cleaned

        # 2. Check tel: links
        tel_links = sel.css('a[href^="tel:"]::attr(href)').getall()
        for link in tel_links:
            num = link.replace("tel:", "").strip()
            cleaned = clean_phone_number(num)
            if cleaned:
                logger.info("Found phone via tel: link: %s", cleaned)
                return cleaned

        # 3. Check Zalo links (e.g. https://zalo.me/0912345678)
        zalo_links = sel.css('a[href*="zalo.me/"]::attr(href)').getall()
        for link in zalo_links:
            m = re.search(r"zalo\.me/(0[35789]\d{8})", link)
            if m:
                cleaned = clean_phone_number(m.group(1))
                if cleaned:
                    logger.info("Found phone via Zalo link: %s", cleaned)
                    return cleaned

        # 4. Check hidden input fields
        hidden_inputs = sel.css(
            'input[type="hidden"][name*="phone"]::attr(value), input[type="hidden"][id*="phone"]::attr(value)'
        ).getall()
        for val in hidden_inputs:
            cleaned = clean_phone_number(val)
            if cleaned:
                logger.info("Found phone via hidden input: %s", cleaned)
                return cleaned

        # 5. Check inline JSON scripts (e.g. Next.js __NEXT_DATA__, Schema.org JSON-LD, or contact objects)
        script_matches = re.findall(
            r'["\'](?:telephone|phone|phoneNumber|contact_phone|mobile)["\']\s*[:=]\s*["\']([0-9\+\s\.\-]{9,15})["\']',
            html,
            re.IGNORECASE,
        )
        for raw in script_matches:
            cleaned = clean_phone_number(raw)
            if cleaned:
                logger.info("Found phone via inline script JSON: %s", cleaned)
                return cleaned

        return None

    async def _extract_via_headless(self, url: str) -> Optional[str]:
        """Tier 3: Click-to-reveal via Playwright headless browser."""
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            logger.debug("Playwright not installed; skipping headless button click for %s", url)
            return None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(
                    user_agent=self.headers["User-Agent"],
                )
                await page.goto(
                    url, timeout=int(self.timeout_sec * 1000), wait_until="domcontentloaded"
                )

                # Try clicking known reveal buttons
                for selector in REVEAL_BUTTON_SELECTORS:
                    try:
                        elem = page.locator(selector).first
                        if await elem.is_visible(timeout=1000):
                            logger.info("Clicking phone button '%s' on %s", selector, url)
                            await elem.click(timeout=2000)
                            await page.wait_for_timeout(1000)
                            # After click, check page content or element text
                            content = await page.content()
                            phone = self.extract_from_html(content)
                            if phone:
                                await browser.close()
                                return phone
                    except Exception:
                        continue

                await browser.close()
        except Exception as exc:
            logger.warning("Headless phone extraction failed for %s: %s", url, exc)

        return None

    def extract_email_from_html(self, html: str) -> Optional[str]:
        """Extract unmasked advertiser email from HTML string using multi-tier heuristics."""
        if not html:
            return None

        sel = Selector(text=html)

        # 1. Check Cloudflare data-cfemail attribute
        cf_matches = sel.css(
            "[data-cfemail]::attr(data-cfemail), span.__cf_email__::attr(data-cfemail)"
        ).getall()
        for cf in cf_matches:
            decoded = decode_cloudflare_email(cf)
            if decoded:
                cleaned = clean_email(decoded)
                if cleaned:
                    logger.info("Found email via Cloudflare data-cfemail: %s", cleaned)
                    return cleaned

        # 2. Check Homedy chat attributes (chatpostinfo_..., pj_...)
        chat_matches = sel.css(
            'a[data-id*="chatpostinfo_"]::attr(data-id), div[data-id*="chatpostinfo_"]::attr(data-id), [id*="pj_"]::attr(id)'
        ).getall()
        for ch in chat_matches:
            cleaned = clean_email(ch)
            if cleaned:
                logger.info("Found email via chat attribute: %s", cleaned)
                return cleaned

        # 3. Check data-email / data-contact-email attributes
        for attr in ["data-email", "data-contact-email", "data-user-email"]:
            val = sel.css(f"[{attr}]::attr({attr})").get()
            cleaned = clean_email(val)
            if cleaned:
                logger.info("Found email via attribute [%s]: %s", attr, cleaned)
                return cleaned

        # 4. Check mailto: links
        mailto_links = sel.css('a[href^="mailto:"]::attr(href)').getall()
        for link in mailto_links:
            cleaned = clean_email(link)
            if cleaned:
                logger.info("Found email via mailto: link: %s", cleaned)
                return cleaned

        # 5. Check avatar URL containing email (Cafeland)
        avatar_urls = sel.css(
            'img.avatar::attr(src), .user-avatar img::attr(src), img[src*="@"]::attr(src)'
        ).getall()
        for src in avatar_urls:
            cleaned = clean_email(src)
            if cleaned:
                logger.info("Found email via avatar src: %s", cleaned)
                return cleaned

        # 6. Fallback: regex scan text content
        return extract_email_from_text(html)
