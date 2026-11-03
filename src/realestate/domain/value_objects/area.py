from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class Area(BaseModel):
    """Real estate area representation in square meters."""

    model_config = ConfigDict(frozen=True)

    m2: float = Field(..., gt=0.0)

    @property
    def value(self) -> float:
        return self.m2

