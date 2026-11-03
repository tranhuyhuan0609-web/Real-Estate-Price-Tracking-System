from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class AdvertiserKind(str, Enum):
    BROKER = "broker"
    OWNER = "owner"
    UNKNOWN = "unknown"


class AdvertiserData(BaseModel):
    """Extracted advertiser information."""

    advertiser_id: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    kind: AdvertiserKind = AdvertiserKind.UNKNOWN
    is_verified: bool = False

