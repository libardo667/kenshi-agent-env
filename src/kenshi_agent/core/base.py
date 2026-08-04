"""Base domain types."""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
