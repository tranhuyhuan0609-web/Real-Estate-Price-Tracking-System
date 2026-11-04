"""Absolute, epoch and relative ("2 giờ trước") Vietnamese datetimes."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


def parse_vietnamese_datetime(raw: Any) -> Optional[datetime]:
    """Parse timestamps from ISO strings, UNIX epoch (sec/ms), or Vietnamese relative/absolute text."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw

    # 1. Numeric UNIX timestamps
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e11:  # milliseconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None

    text = str(raw).strip()
    if text.isdigit() and len(text) in (10, 13):
        ts = float(text)
        if len(text) == 13:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None

    # 2. Vietnamese relative strings
    now = datetime.now(timezone.utc)
    text_lower = text.lower()

    m_sec = re.search(r"(\d+)\s*(?:giây|giay|s)\s*trước", text_lower)
    if m_sec:
        return now - timedelta(seconds=int(m_sec.group(1)))

    m_min = re.search(r"(\d+)\s*(?:phút|phut|p)\s*trước", text_lower)
    if m_min:
        return now - timedelta(minutes=int(m_min.group(1)))

    m_hour = re.search(r"(\d+)\s*(?:giờ|gio|h)\s*trước", text_lower)
    if m_hour:
        return now - timedelta(hours=int(m_hour.group(1)))

    m_day = re.search(r"(\d+)\s*(?:ngày|ngay)\s*trước", text_lower)
    if m_day:
        return now - timedelta(days=int(m_day.group(1)))

    if "hôm nay" in text_lower or "hom nay" in text_lower:
        m_time = re.search(r"(\d{1,2}):(\d{2})", text_lower)
        if m_time:
            h, mi = int(m_time.group(1)), int(m_time.group(2))
            return now.replace(hour=h, minute=mi, second=0, microsecond=0)
        return now

    if "hôm qua" in text_lower or "hom qua" in text_lower:
        m_time = re.search(r"(\d{1,2}):(\d{2})", text_lower)
        yest = now - timedelta(days=1)
        if m_time:
            h, mi = int(m_time.group(1)), int(m_time.group(2))
            return yest.replace(hour=h, minute=mi, second=0, microsecond=0)
        return yest

    # 3. Standard DD/MM/YYYY or DD-MM-YYYY
    m_date = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if m_date:
        d, m, y = int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3))
        h = int(m_date.group(4)) if m_date.group(4) else 0
        mi = int(m_date.group(5)) if m_date.group(5) else 0
        s = int(m_date.group(6)) if m_date.group(6) else 0
        try:
            return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)
        except ValueError:
            pass

    # 4. ISO fallback
    try:
        clean_str = text.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_str)
    except (ValueError, TypeError):
        return None
