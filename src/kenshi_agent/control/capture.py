from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageGrab

from .base import InputController


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    path: Path
    sha256: str
    width: int
    height: int


class WindowCapture:
    def __init__(
        self,
        controller: InputController,
        run_dir: Path,
        *,
        image_format: str = "png",
        jpeg_quality: int = 90,
    ) -> None:
        self.controller = controller
        self.run_dir = run_dir
        self.image_format = image_format
        self.jpeg_quality = jpeg_quality
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def capture(self, sequence: int) -> CapturedFrame:
        if os.name != "nt":
            raise RuntimeError("Live window capture is available only on Windows.")
        rect = self.controller.client_rect()
        image = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom),
            all_screens=True,
        )
        extension = "jpg" if self.image_format == "jpeg" else "png"
        path = self.run_dir / f"live_frame_{sequence:06d}.{extension}"
        if self.image_format == "jpeg":
            image.convert("RGB").save(path, quality=self.jpeg_quality)
        else:
            image.save(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return CapturedFrame(path=path, sha256=digest, width=image.width, height=image.height)
