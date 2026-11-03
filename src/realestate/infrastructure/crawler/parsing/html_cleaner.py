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

def clean_email(raw: Any) -> Optional[str]:
    """Normalize and validate email address, unmasking [email protected] if possible."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if "@" in s and not s.startswith("http"):
        m = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", s)
        if m:
            cand = m.group(0).lower()
            if not any(token in cand for token in ["protected", "example.com"]):
                return cand
    return None

def extract_email_from_text(text: Optional[str]) -> Optional[str]:
    """Scan freeform text for valid email address."""
    if not text:
        return None
    matches = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    for m in matches:
        cleaned = clean_email(m)
        if cleaned:
            return cleaned
    return None
