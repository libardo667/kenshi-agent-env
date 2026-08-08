"""Mutable run-level primitive-action accounting, separate from policy."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from .config import SafetyConfig
from .core.authority import AuthorizationCode
from .operation_definitions import BoundOperation


class ActionBudgetError(RuntimeError):
    """A mutable run budget cannot reserve the requested transaction."""

    code = AuthorizationCode.TRANSACTION_BUDGET_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class ActionBudgetReservation:
    """Exact pending run capacity for one already-authorized operation."""

    bound: BoundOperation
    primitive_actions: int
    token: int


class ActionBudgetLedger:
    """Reserve, commit, and release mutable run-level action capacity."""

    def __init__(self, config: SafetyConfig) -> None:
        self.config = config
        self._action_times: deque[float] = deque()
        self._pending_primitive_count = 0
        self._next_reservation_token = 1
        self._reservations: dict[int, ActionBudgetReservation] = {}

    def reserve(
        self,
        bound: BoundOperation,
    ) -> ActionBudgetReservation:
        action = bound.operation
        primitive_actions = bound.definition.primitive_action_bound_for(action)

        self._reserve_rate_budget(primitive_actions)
        token = self._next_reservation_token
        self._next_reservation_token += 1
        reservation = ActionBudgetReservation(
            bound=bound,
            primitive_actions=primitive_actions,
            token=token,
        )
        self._reservations[token] = reservation
        return reservation

    def commit(self, reservation: ActionBudgetReservation) -> None:
        self._take_reservation(reservation)
        self._pending_primitive_count -= reservation.primitive_actions
        now = time.monotonic()
        self._prune_rate_budget(now)
        self._action_times.extend([now] * reservation.primitive_actions)

    def release(self, reservation: ActionBudgetReservation) -> None:
        self._take_reservation(reservation)
        self._pending_primitive_count -= reservation.primitive_actions

    def _take_reservation(self, reservation: ActionBudgetReservation) -> None:
        active = self._reservations.pop(reservation.token, None)
        if active is not reservation:
            raise RuntimeError("Action budget reservation is foreign or already finalized.")

    def _prune_rate_budget(self, now: float) -> None:
        cutoff = now - 60.0
        while self._action_times and self._action_times[0] < cutoff:
            self._action_times.popleft()

    def _reserve_rate_budget(self, count: int) -> None:
        now = time.monotonic()
        self._prune_rate_budget(now)
        if (
            len(self._action_times) + self._pending_primitive_count + count
            > self.config.max_actions_per_minute
        ):
            raise ActionBudgetError("Per-minute primitive action rate limit would be exceeded.")
        self._pending_primitive_count += count
