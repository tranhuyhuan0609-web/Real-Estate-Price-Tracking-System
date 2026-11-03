from __future__ import annotations
import re
from typing import Any, Optional

def parse_vietnamese_phone(raw: Any) -> Optional[str]:
    """Extract and normalize standard Vietnamese phone number (10 digits starting with 0)."""
    if raw is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(raw).strip())
    if len(digits) == 11 and digits.startswith("84"):
        digits = "0" + digits[2:]
    elif len(digits) == 12 and digits.startswith("0084"):
        digits = "0" + digits[4:]

    if len(digits) == 10 and re.match(r"^0[3|5|7|8|9][0-9]{8}$", digits):
        return digits
    return None

def extract_phone_from_text(text: Optional[str]) -> Optional[str]:
    """Scan freeform text for valid Vietnamese phone numbers."""
    if not text:
        return None
    patterns = [
        r"(?:\+84|84|0)[3|5|7|8|9][0-9]{8}\b",
        r"(?:\+84|84|0)[3|5|7|8|9](?:[.\s-][0-9]{2,4}){2,4}\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            cand = parse_vietnamese_phone(match.group(0))
            if cand:
                return cand
    return None
