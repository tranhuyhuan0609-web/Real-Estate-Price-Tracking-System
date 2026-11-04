"""Property fingerprint used to detect the same property listed on several sites."""

from __future__ import annotations

import hashlib
import re
from typing import Optional


def generate_fingerprint(
    *,
    title: str,
    area_m2: Optional[float] = None,
    city_name: Optional[str] = None,
    district_name: Optional[str] = None,
    ward_name: Optional[str] = None,
    advertiser_phone: Optional[str] = None,
    city_code: Optional[str] = None,
    district_code: Optional[str] = None,
    ward_code: Optional[str] = None,
) -> str:
    """Generate SHA-256 property fingerprint for cross-site deduplication and tracking.
    Excludes advertiser_phone so phone masking/revealing does not alter property identity.
    """
    norm_title = re.sub(r"[^\w\s]", "", title.lower())
    norm_title = " ".join(norm_title.split())

    norm_area = f"{round(area_m2, 1)}" if area_m2 and area_m2 > 0 else ""
    norm_city = (city_name or city_code or "").strip().lower()
    norm_district = (district_name or district_code or "").strip().lower()
    norm_ward = (ward_name or ward_code or "").strip().lower()

    raw_signature = f"{norm_title}|{norm_area}|{norm_city}|{norm_district}|{norm_ward}"
    return hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()
