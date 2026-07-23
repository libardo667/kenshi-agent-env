from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class ControlOwnershipState(StrEnum):
    AGENT_ACTIVE = "agent_active"
    HUMAN_CONTROL = "human_control"
    TAKEOVER_PENDING = "takeover_pending"
    DISARMED = "disarmed"


class ControlOwnershipEventType(StrEnum):
    CHANGED = "control_ownership_changed"
    COUNTDOWN = "agent_takeover_countdown"
    CANCELLED = "agent_takeover_cancelled"
    READY = "agent_takeover_ready"


@dataclass(frozen=True, slots=True)
class ControlOwnershipEvent:
    event_type: ControlOwnershipEventType
    state: ControlOwnershipState
    reason: str
    seconds_remaining: int | None = None


class ControlOwnershipMachine:
    """Deterministic ownership lifecycle for visible human/agent handoff."""

    def __init__(
        self,
        *,
        quiet_seconds: float,
        countdown_seconds: float,
    ) -> None:
        if quiet_seconds < 0:
            raise ValueError("quiet_seconds must be non-negative.")
        if countdown_seconds <= 0:
            raise ValueError("countdown_seconds must be positive.")
        self.quiet_seconds = quiet_seconds
        self.countdown_seconds = countdown_seconds
        self.state = ControlOwnershipState.AGENT_ACTIVE
        self._last_human_input_at: float | None = None
        self._takeover_at: float | None = None
        self._last_announced_second: int | None = None

    def yield_to_human(
        self,
        now: float,
        *,
        reason: str,
    ) -> tuple[ControlOwnershipEvent, ...]:
        self.state = ControlOwnershipState.HUMAN_CONTROL
        self._last_human_input_at = now
        self._takeover_at = None
        self._last_announced_second = None
        return (
            ControlOwnershipEvent(
                event_type=ControlOwnershipEventType.CHANGED,
                state=self.state,
                reason=reason,
            ),
        )

    def advance(
        self,
        now: float,
        *,
        human_input: bool = False,
        emergency_stop: bool = False,
    ) -> tuple[ControlOwnershipEvent, ...]:
        if emergency_stop:
            return self.disarm(
                reason="Emergency stop pressed; automatic agent takeover is disarmed."
            )
        if self.state is ControlOwnershipState.DISARMED:
            return ()

        events: list[ControlOwnershipEvent] = []
        if human_input:
            if self.state is ControlOwnershipState.TAKEOVER_PENDING:
                events.append(
                    ControlOwnershipEvent(
                        event_type=ControlOwnershipEventType.CANCELLED,
                        state=ControlOwnershipState.HUMAN_CONTROL,
                        reason="Human input reset the pending agent takeover countdown.",
                    )
                )
            changed = self.state is not ControlOwnershipState.HUMAN_CONTROL
            self.state = ControlOwnershipState.HUMAN_CONTROL
            self._last_human_input_at = now
            self._takeover_at = None
            self._last_announced_second = None
            if changed:
                events.append(
                    ControlOwnershipEvent(
                        event_type=ControlOwnershipEventType.CHANGED,
                        state=self.state,
                        reason="Human input retained control.",
                    )
                )
            return tuple(events)

        if self.state is ControlOwnershipState.HUMAN_CONTROL:
            last_input = self._last_human_input_at
            if last_input is None:
                self._last_human_input_at = now
                return ()
            if now - last_input < self.quiet_seconds:
                return ()
            self.state = ControlOwnershipState.TAKEOVER_PENDING
            self._takeover_at = now + self.countdown_seconds
            self._last_announced_second = None
            events.append(
                ControlOwnershipEvent(
                    event_type=ControlOwnershipEventType.CHANGED,
                    state=self.state,
                    reason=(
                        "The configured quiet interval elapsed; visible agent "
                        "takeover countdown started."
                    ),
                )
            )

        if self.state is not ControlOwnershipState.TAKEOVER_PENDING:
            return tuple(events)
        takeover_at = self._takeover_at
        if takeover_at is None:
            raise RuntimeError("Pending takeover has no deadline.")
        remaining = max(0.0, takeover_at - now)
        if remaining <= 0:
            self.state = ControlOwnershipState.AGENT_ACTIVE
            self._takeover_at = None
            self._last_announced_second = None
            events.extend(
                (
                    ControlOwnershipEvent(
                        event_type=ControlOwnershipEventType.READY,
                        state=self.state,
                        reason=(
                            "The visible takeover countdown completed; current "
                            "state must still be revalidated before agent work."
                        ),
                        seconds_remaining=0,
                    ),
                    ControlOwnershipEvent(
                        event_type=ControlOwnershipEventType.CHANGED,
                        state=self.state,
                        reason="Agent takeover countdown completed.",
                    ),
                )
            )
            return tuple(events)

        announced = max(1, math.ceil(remaining))
        if announced != self._last_announced_second:
            self._last_announced_second = announced
            events.append(
                ControlOwnershipEvent(
                    event_type=ControlOwnershipEventType.COUNTDOWN,
                    state=self.state,
                    reason=(
                        "Do not touch the keyboard or mouse unless you want to "
                        "keep human control."
                    ),
                    seconds_remaining=announced,
                )
            )
        return tuple(events)

    def disarm(self, *, reason: str) -> tuple[ControlOwnershipEvent, ...]:
        if self.state is ControlOwnershipState.DISARMED:
            return ()
        events: list[ControlOwnershipEvent] = []
        if self.state is ControlOwnershipState.TAKEOVER_PENDING:
            events.append(
                ControlOwnershipEvent(
                    event_type=ControlOwnershipEventType.CANCELLED,
                    state=ControlOwnershipState.DISARMED,
                    reason=reason,
                )
            )
        self.state = ControlOwnershipState.DISARMED
        self._takeover_at = None
        self._last_announced_second = None
        events.append(
            ControlOwnershipEvent(
                event_type=ControlOwnershipEventType.CHANGED,
                state=self.state,
                reason=reason,
            )
        )
        return tuple(events)
