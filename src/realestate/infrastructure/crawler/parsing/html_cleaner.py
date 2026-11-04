"""HTML cleanup and advertiser email unmasking (Cloudflare, mailto, avatars)."""

from __future__ import annotations

import html
import re
from typing import Optional


def clean_html(raw_html: Optional[str]) -> Optional[str]:
    """Strip HTML tags, unescape entities, and normalize whitespace."""
    if not raw_html:
        return None
    text = html.unescape(raw_html)
    text = re.sub(r"<(?:p|div|br|li|h[1-6]|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned if cleaned else None


# Platform support and generic system domains/prefixes that must NEVER be assigned to listing advertisers
PLATFORM_SUPPORT_DOMAINS = {
    "homedy.com",
    "mogi.vn",
    "cafeland.vn",
    "meeyland.com",
    "meeygroup.com",
    "chotot.vn",
    "chotot.com",
    "batdongsan.vn",
    "batdongsan.com.vn",
    "muaban.net",
}


PLATFORM_SUPPORT_PREFIXES = {
    "lienhe",
    "trogiup",
    "info",
    "contact",
    "support",
    "hotro",
    "cskh",
    "admin",
    "noreply",
    "no-reply",
    "sale",
    "sales",
    "banhang",
    "bientap",
}


def decode_cloudflare_email(cf_hex: Optional[str]) -> Optional[str]:
    """Decode Cloudflare XOR-obfuscated email from data-cfemail hex attribute.

    Cloudflare uses a 1-byte XOR cipher where the first 2 hex digits are the XOR key,
    and each subsequent 2 hex digits represent one XOR-encoded character.
    """
    if not cf_hex or not isinstance(cf_hex, str):
        return None
    raw = cf_hex.strip()
    if len(raw) < 6 or len(raw) % 2 != 0:
        return None
    try:
        key = int(raw[:2], 16)
        chars = [chr(int(raw[i : i + 2], 16) ^ key) for i in range(2, len(raw), 2)]
        email = "".join(chars).strip().lower()
        if re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email):
            return email
    except (ValueError, TypeError):
        pass
    return None


def clean_email(raw_email: Optional[str]) -> Optional[str]:
    """Normalize, unmask, and validate an email address.

    Returns None if:
    - The email is masked with asterisks or dots (e.g. 'abc***@gmail.com')
    - The email is a platform support / generic portal email
    - The string is not a valid RFC-compliant email address
    """
    if not raw_email:
        return None

    text = str(raw_email).strip()

    # Reject masked emails immediately (containing '*' or '...') to avoid corrupting database
    if "*" in text or "..." in text:
        return None

    # Check if this is a Cloudflare hex string (e.g. 4135332e2628...)
    if re.fullmatch(r"^[0-9a-fA-F]{10,}$", text):
        decoded = decode_cloudflare_email(text)
        if decoded:
            text = decoded

    # Strip mailto: prefix and URL query parameters
    text = re.sub(r"^mailto:\s*", "", text, flags=re.IGNORECASE)
    text = text.split("?")[0].strip()

    # Strip Homedy chat prefixes (chatpostinfo_..., pj_...)
    text = re.sub(r"^(?:chatpostinfo_|pj_)", "", text, flags=re.IGNORECASE)

    # Check for Cafeland/Mogi avatar filename containing email (e.g. /avatar/name@gmail.com-timestamp.jpg)
    m_avatar = re.search(
        r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9]+(?:\.[a-zA-Z0-9]+)*\.(?:com\.vn|com|vn|net|org|edu|gov|io|info|biz|me))\b",
        text,
        re.IGNORECASE,
    )
    if m_avatar:
        text = m_avatar.group(1)

    text = text.strip().lower()

    if "*" in text or "..." in text:
        return None

    # Validate email format
    if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", text):
        return None

    # Filter out generic portal customer service emails
    try:
        user_part, domain_part = text.split("@", 1)
        if domain_part in PLATFORM_SUPPORT_DOMAINS and user_part in PLATFORM_SUPPORT_PREFIXES:
            return None
    except ValueError:
        return None

    return text


def extract_email_from_text(text: Optional[str]) -> Optional[str]:
    """Scan arbitrary text (title, description, raw HTML) for valid, unmasked advertiser emails."""
    if not text:
        return None

    # Find candidate emails in text
    candidates = re.findall(r"\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b", str(text))
    for cand in candidates:
        cleaned = clean_email(cand)
        if cleaned:
            return cleaned
    return None
