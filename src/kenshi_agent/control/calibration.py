from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..models import Vec2


class CalibrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalibrationWindow(CalibrationModel):
    expected_width: int = Field(gt=0)
    expected_height: int = Field(gt=0)
    ui_scale: float = Field(default=1.0, gt=0.0)


class Calibration(CalibrationModel):
    version: int = 1
    window: CalibrationWindow
    anchors: dict[str, Vec2]

    @classmethod
    def load(cls, path: Path) -> Calibration:
        with path.open("r", encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle) or {})

    def validate_window(self, width: int, height: int, *, tolerance_px: int = 2) -> None:
        if (
            abs(width - self.window.expected_width) > tolerance_px
            or abs(height - self.window.expected_height) > tolerance_px
        ):
            raise ValueError(
                f"Client area {width}x{height} does not match calibration "
                f"{self.window.expected_width}x{self.window.expected_height}."
            )
