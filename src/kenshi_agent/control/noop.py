from __future__ import annotations

from datetime import UTC, datetime

from ..models import ActionReceipt
from .base import InputController, PrimitiveInputAction, WindowRect


class NoopInputController(InputController):
    def __init__(self, *, message: str = "Dry-run input controller.") -> None:
        self.message = message

    def focus_window(self) -> None:
        return None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        now = datetime.now(UTC)
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=False,
            dry_run=True,
            started_at=now,
            finished_at=now,
            primitive_actions=1,
            message=self.message,
        )

    def emergency_stop_pressed(self, key: str) -> bool:
        return False

    def client_rect(self) -> WindowRect:
        return WindowRect(left=0, top=0, right=1920, bottom=1080)
