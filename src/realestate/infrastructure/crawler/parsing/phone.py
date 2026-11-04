"""Vietnamese phone number normalisation and extraction."""

from __future__ import annotations

import re
from typing import Any, Optional


def parse_vietnamese_phone(raw: Any) -> Optional[str]:
    """Extract and normalize standard Vietnamese phone number (pure digits, starting with 0).

    Strictly matches valid Vietnamese mobile (10 digits: 03, 05, 07, 08, 09)
    or landline (10-11 digits starting with 02). Returns None if invalid.
    """
    if not raw:
        return None
    text = str(raw).strip()
    text = re.sub(r"^(?:tel:|phone:|callto:)\s*", "", text, flags=re.IGNORECASE)

    # First normalize pure digits
    clean = re.sub(r"\D", "", text)
    if clean.startswith("84"):
        clean = "0" + clean[2:]
    elif not clean.startswith("0") and len(clean) == 9 and clean[0] in "35789":
        clean = "0" + clean

    # Valid mobile numbers (10 digits: 03x, 05x, 07x, 08x, 09x)
    if len(clean) == 10 and re.match(r"^0[35789]\d{8}$", clean):
        return clean
    # Valid landline numbers (10 or 11 digits: 02x)
    if len(clean) in (10, 11) and re.match(r"^02\d{8,9}$", clean):
        return clean

    # Fallback to search inside URLs like /moi-gioi/0901234567 or zalo.me/0901234567
    m = re.search(r"(?:/moi-gioi/|tel:|phone:|zalo\.me/)(?:\+?84|0)?([35789]\d{8})\b", text)
    if m:
        return "0" + m.group(1)

    return None


def extract_phone_from_text(text: Optional[str]) -> Optional[str]:
    """Scan arbitrary text (title, description) for Vietnamese mobile phone numbers."""
    if not text:
        return None
    # Strip parentheses and brackets that break phone token regex
    cleaned = re.sub(r"[\(\)\[\]]", " ", str(text))
    # Normalize common obfuscations: replace 'o' or 'O' with '0' in phone-like tokens
    cleaned = re.sub(r"\b[oO](?=[35789])", "0", cleaned)
    cleaned = re.sub(r"(\d)[oO](\d)", r"\g<1>0\g<2>", cleaned)
    cleaned = re.sub(r"(\d)[oO]\b", r"\g<1>0", cleaned)
    m = re.search(r"(?:\+?84|0)([35789](?:[.\s-]{0,2}\d){8})\b", cleaned)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 9:
            return "0" + digits
    return None
