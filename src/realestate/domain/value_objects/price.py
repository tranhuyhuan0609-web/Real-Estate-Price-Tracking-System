from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class PriceStatus(str, Enum):
    INITIAL = "initial"
    INCREASED = "increased"
    DECREASED = "decreased"
    UNCHANGED = "unchanged"
    CONTACT = "contact"


class PriceChangeEvaluation(BaseModel):
    price_diff_vnd: float
    price_diff_percent: float
    price_status: PriceStatus
