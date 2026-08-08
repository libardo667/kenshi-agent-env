from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.observation import Observation
from ..core.operation import Action
from ..core.transport import (
    ActionReceipt,
    CommandDispatchContext,
    Transition,
)
from .base import AgentEnvironment

if TYPE_CHECKING:
    from ..input_boundary import ExecutionToken


class ReplayOperationPort:
    """Exact dry-run operation surface for replayed evidence."""

    def __init__(self, environment: ReplayEnvironment) -> None:
        self._environment = environment

    async def _execute(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
    ) -> Transition:
        del token
        started = datetime.now(UTC)
        observation = await self._environment.advance()
        receipt = ActionReceipt(
            action=action,
            control_mode=observation.control_mode,
            accepted=True,
            executed=False,
            dry_run=True,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=0,
            message="Replay operation does not emit input.",
            command_id=command.command_id,
            started_after_revision=command.based_on_revision,
            completed_at_revision=observation.world_revision,
            causal_revision_advanced=observation.world_revision.is_later_than(
                command.based_on_revision
            ),
        )
        return Transition(
            receipt=receipt,
            observation=observation,
            terminated=self._environment.at_end,
            success=None,
        )

    approach_dialogue_target = _execute
    exit_current_building = _execute
    move_in_direction = _execute
    move_to_character = _execute
    pause = _execute
    perform_context_action = _execute
    perform_character_order = _execute
    produce_resource_output = _execute
    regroup_with_squad_member = _execute
    respond_to_immediate_threat = _execute
    select_squad_member_exact = _execute
    set_speed = _execute
    travel_to_map_destination = _execute
    wait = _execute

    async def control_pause(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
    ) -> Transition:
        return await self._execute(action, command=command, token=None)


class ReplayEnvironment(AgentEnvironment):
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self._observations = self._load_observations(log_path)
        self.control_mode = self._observations[0].control_mode
        self._index = 0
        self._mechanics = ReplayOperationPort(self)

    @property
    def operation_mechanics(self) -> ReplayOperationPort:
        return self._mechanics

    @staticmethod
    def _load_observations(path: Path) -> list[Observation]:
        observations: list[Observation] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                if record.get("event_type") == "observation":
                    payload = record["payload"]
                    if isinstance(payload, dict) and payload.get("digest"):
                        raise ValueError(
                            f"{path} was recorded with observation digests, which omit "
                            "most of the observation and cannot be replayed. Re-record "
                            "the run with runtime.log_full_observations: true."
                        )
                    observations.append(Observation.model_validate(payload))
        if not observations:
            raise ValueError(f"No observation events found in {path}.")
        return observations

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        self._index = 0
        return self._observations[0].model_copy(update={"mode": "replay"})

    async def observe(self) -> Observation:
        return self._observations[self._index].model_copy(update={"mode": "replay"})

    async def advance(self) -> Observation:
        """Advance the deterministic replay observation stream without an action."""

        if self._index + 1 < len(self._observations):
            self._index += 1
        return await self.observe()

    @property
    def at_end(self) -> bool:
        return self._index + 1 >= len(self._observations)

    async def close(self) -> None:
        return None
