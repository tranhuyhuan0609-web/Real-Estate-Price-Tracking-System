from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

def parse_relative_datetime(raw: Any) -> Optional[datetime]:
    """Parse relative or absolute Vietnamese datetime string into UTC datetime."""
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        try:
            val = float(raw)
            if val > 1e11:  # milliseconds
                val /= 1000
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except (ValueError, OSError):
            return None

    text = str(raw).strip().lower()
    now = datetime.now(timezone.utc)

    # Relative expressions
    m = re.search(r"(\d+)\s*(?:phút|phut|min)", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.search(r"(\d+)\s*(?:giờ|gio|hour|h\b)", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r"(\d+)\s*(?:ngày|ngay|day)", text)
    if m:
        return now - timedelta(days=int(m.group(1)))
    if "hôm nay" in text or "hom nay" in text or "today" in text:
        return now
    if "hôm qua" in text or "hom qua" in text or "yesterday" in text:
        return now - timedelta(days=1)

    # Standard formats
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(text[:26].strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
