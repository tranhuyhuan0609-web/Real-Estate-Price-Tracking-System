"""Absolute, epoch and relative ("2 giờ trước") Vietnamese datetimes."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Optional
from zoneinfo import ZoneInfo

VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# "<n> <unit> trước" -> timedelta keyword; checked in this order.
_RELATIVE = (
    (re.compile(r"(\d+)\s*(?:giây|giay|s)\s*trước"), "seconds"),
    (re.compile(r"(\d+)\s*(?:phút|phut|p)\s*trước"), "minutes"),
    (re.compile(r"(\d+)\s*(?:giờ|gio|h)\s*trước"), "hours"),
    (re.compile(r"(\d+)\s*(?:ngày|ngay)\s*trước"), "days"),
)
_DAY_WORDS = ((("hôm nay", "hom nay"), 0), (("hôm qua", "hom qua"), 1))
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
_DMY = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")


def parse_vietnamese_datetime(
    raw: Any,
    default_tz: tzinfo = VIETNAM_TZ,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Parse ISO strings, UNIX epochs (s/ms), "dd/mm/yyyy [hh:mm[:ss]]" and Vietnamese relative
    text ("5 phút trước", "hôm qua 14:30"). Naive values are in ``default_tz``; result is UTC."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        aware: datetime = raw if raw.tzinfo is not None else raw.replace(tzinfo=default_tz)
        return aware.astimezone(timezone.utc)
    if isinstance(raw, (int, float)):
        return _from_epoch(float(raw), milliseconds=raw > 1e11)

    text = str(raw).strip()
    if text.isdigit() and len(text) in (10, 13):
        return _from_epoch(float(text), milliseconds=len(text) == 13)

    local_now = (now or datetime.now(timezone.utc)).astimezone(default_tz)
    for parse in (_relative, _day_word):
        result = parse(text.lower(), local_now)
        if result is not None:
            return result.astimezone(timezone.utc)
    return _day_month_year(text, default_tz) or _iso(text, default_tz)


def _from_epoch(ts: float, *, milliseconds: bool) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(ts / 1000.0 if milliseconds else ts, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _relative(text: str, now: datetime) -> Optional[datetime]:
    for pattern, unit in _RELATIVE:
        m = pattern.search(text)
        if m:
            return now - timedelta(**{unit: int(m.group(1))})
    return None


def _day_word(text: str, now: datetime) -> Optional[datetime]:
    """ "hôm nay [hh:mm]" / "hôm qua [hh:mm]" """
    for words, days_ago in _DAY_WORDS:
        if any(w in text for w in words):
            day = now - timedelta(days=days_ago)
            clock = _CLOCK.search(text)
            if clock:
                try:
                    return day.replace(
                        hour=int(clock.group(1)),
                        minute=int(clock.group(2)),
                        second=0,
                        microsecond=0,
                    )
                except ValueError:  # "hôm nay 25:99": keep the day, drop the bogus time
                    pass
            return day
    return None


def _day_month_year(text: str, tz: tzinfo) -> Optional[datetime]:
    m = _DMY.search(text)
    if not m:
        return None
    d, mo, y = (int(m.group(i)) for i in (1, 2, 3))
    h, mi, s = (int(m.group(i)) if m.group(i) else 0 for i in (4, 5, 6))
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=tz).astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(text: str, tz: tzinfo) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (dt if dt.tzinfo is not None else dt.replace(tzinfo=tz)).astimezone(timezone.utc)
