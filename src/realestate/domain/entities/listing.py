from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from realestate.domain.entities.advertiser import AdvertiserData


class Category(str, Enum):
    SALE = "sale"
    RENT = "rent"
    PROJECT = "project"


class PropertyType(str, Enum):
    LAND = "land"
    HOUSE = "house"
    APARTMENT = "apartment"
    VILLA = "villa"
    SHOPHOUSE = "shophouse"
    COMMERCIAL = "commercial"
    OTHER = "other"


class ListingStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    HIDDEN = "hidden"


class PriceStatus(str, Enum):
    INITIAL = "initial"
    INCREASED = "increased"
    DECREASED = "decreased"
    UNCHANGED = "unchanged"
    CONTACT = "contact"


class ListingDetailsData(BaseModel):
    """Extracted descriptive, geospatial and legal property details."""

    model_config = ConfigDict(extra="ignore")

    url: str
    title: str
    description: Optional[str] = None
    city_name: Optional[str] = None
    district_name: Optional[str] = None
    ward_name: Optional[str] = None
    lng: Optional[float] = None
    lat: Optional[float] = None
    legal_status: Optional[str] = None
    thumbnail_url: Optional[str] = None
    media_list: Optional[List[str]] = None

    @field_validator("media_list", mode="before")
    @classmethod
    def coerce_media_list(cls, v: Any) -> Optional[List[str]]:
        if not v or not isinstance(v, list):
            return None
        urls: List[str] = []
        for item in v:
            if isinstance(item, str) and item.strip():
                urls.append(item.strip())
            elif isinstance(item, dict):
                u = item.get("url") or item.get("uri") or item.get("src")
                if u and isinstance(u, str) and u.strip():
                    urls.append(u.strip())
        return urls if urls else None


class ParsedListingItem(BaseModel):
    """Standard parsed real estate item produced by Spider."""

    listing_key: str
    source: str
    external_id: Optional[str] = None
    external_code: Optional[int] = None
    property_fingerprint: Optional[str] = None
    category: Category = Category.SALE
    property_type: PropertyType = PropertyType.OTHER
    area_m2: Optional[float] = None
    facade_m: Optional[float] = None
    depth_m: Optional[float] = None
    current_price_vnd: Optional[float] = None
    current_price_per_m2: Optional[float] = None
    published_at: Optional[datetime] = None
    details: ListingDetailsData
    advertiser: Optional[AdvertiserData] = None


class RawEventMessage(BaseModel):
    """Event message dispatched directly to Kafka topic realestate.raw_events."""

    model_config = ConfigDict(extra="ignore")

    listing_key: str
    site_id: str
    crawl_run_id: str
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parsed_data: ParsedListingItem
    raw_payload: Dict[str, Any]

