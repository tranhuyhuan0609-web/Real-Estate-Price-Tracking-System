from typing import Annotated
from pydantic import BaseModel, ConfigDict, StringConstraints

NonBlank = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
    ),
]


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
