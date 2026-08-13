"""Strict, producer-independent specification models for Protocol 2.0.

These types define the next wire shape before the native producer emits it.
They deliberately do not inherit from :class:`TelemetrySnapshot`: Protocol 2.0
is a breaking replacement for the 1.x roster and command topology, not a
compatibility view over it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import Field, StringConstraints, model_validator

from .base import StrictModel
from .capability import CapabilityDescriptor
from .lifecycle import MonitorDisposition

EntityId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
CommandId = Annotated[str, StringConstraints(pattern=r"^cmd-[0-9a-f]{32}$")]

CAPABILITY_DESCRIPTOR = CapabilityDescriptor(
    name="representation.protocol_2_world_model",
    purpose=(
        "Represent bounded world, selection, task, and command collections "
        "without collapsing missing or truncated data."
    ),
    kind="representation",
    owner_component="kenshi_agent.core.protocol_2",
    implementation_ref="kenshi_agent.core.protocol_2.Protocol2WorldModel",
    semantic_effects=("represent.protocol_2_world_model",),
    proof_key="protocol_2_world_model",
)

CAPABILITY_DESCRIPTORS = (
    CAPABILITY_DESCRIPTOR,
    CapabilityDescriptor(
        name="representation.player_topology",
        purpose="Represent bounded player roster, membership, primary, and selection topology.",
        kind="representation",
        owner_component="kenshi_agent.core.protocol_2",
        implementation_ref="kenshi_agent.core.protocol_2.Protocol2WorldModel",
        semantic_effects=("represent.player_topology",),
        proof_key="player_topology",
    ),
    CapabilityDescriptor(
        name="representation.task_channels",
        purpose=(
            "Represent retained orders, configured jobs, and current task activity "
            "as distinct channels."
        ),
        kind="representation",
        owner_component="kenshi_agent.core.protocol_2",
        implementation_ref="kenshi_agent.core.protocol_2.Protocol2WorldModel",
        semantic_effects=("represent.task_channels",),
        proof_key="task_channels",
    ),
)

ItemT = TypeVar("ItemT")


class CollectionCompleteness(StrEnum):
    """Whether absence from a bounded collection is meaningful."""

    COMPLETE = "complete"
    TRUNCATED = "truncated"


class BoundedCollection(StrictModel, Generic[ItemT]):
    """A bounded collection with one non-contradictory completeness signal.

    ``known_total`` is exact when present. A truncated source that cannot know
    its total leaves it null; it never substitutes the retained item count.
    """

    items: list[ItemT] = Field(max_length=256)
    completeness: CollectionCompleteness
    known_total: int | None = Field(ge=0)

    @model_validator(mode="after")
    def completeness_matches_items(self) -> BoundedCollection[ItemT]:
        retained = len(self.items)
        if self.completeness is CollectionCompleteness.COMPLETE:
            if self.known_total != retained:
                raise ValueError(
                    "a complete collection requires known_total equal to len(items)"
                )
        elif self.known_total is not None and self.known_total <= retained:
            raise ValueError(
                "a truncated collection's known_total must exceed len(items)"
            )
        return self


class EntityIdSet(BoundedCollection[EntityId]):
    """A set serialized in deterministic ID order."""

    @model_validator(mode="after")
    def ids_are_unique_and_canonical(self) -> EntityIdSet:
        if len(self.items) != len(set(self.items)):
            raise ValueError("entity ID sets must not contain duplicates")
        if self.items != sorted(self.items):
            raise ValueError("entity ID sets must be serialized in sorted order")
        return self


class TaskEvidence(StrictModel):
    """One task read from one exact Kenshi task channel."""

    task_value: int
    task_name: str = Field(min_length=1, max_length=80)
    subject_id: EntityId | None
    description: str | None = Field(max_length=300)
    # Exact source-container index when the inspected API proves it. Null for
    # current activity and for sampled order-tail entries whose index is unknown.
    position: int | None = Field(ge=0)


class CharacterWorkState(StrictModel):
    """Kenshi work channels kept separate rather than inferred."""

    has_player_orders: bool
    ordinary_orders: BoundedCollection[TaskEvidence]
    jobs_enabled: bool
    jobs: BoundedCollection[TaskEvidence]
    permanent_jobs: BoundedCollection[TaskEvidence]
    current_activity: TaskEvidence | None


class PlayerCharacterState(StrictModel):
    """One member of the player roster, independent of UI selection."""

    id: EntityId
    name: str = Field(min_length=1, max_length=200)
    platoon_id: EntityId | None
    work: CharacterWorkState | None


class PlatoonState(StrictModel):
    """One player platoon and its observed membership."""

    id: EntityId
    name: str | None = Field(max_length=200)
    member_ids: EntityIdSet


class DispatchBasis(StrictModel):
    """The immutable identities against which one command was dispatched."""

    primary_character_id: EntityId | None
    selected_character_ids: list[EntityId] = Field(max_length=64)
    active_platoon_id: EntityId | None
    recipient_ids: list[EntityId] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def identity_sets_are_exact(self) -> DispatchBasis:
        for label, values in (
            ("selected_character_ids", self.selected_character_ids),
            ("recipient_ids", self.recipient_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must not contain duplicates")
            if values != sorted(values):
                raise ValueError(f"{label} must be serialized in sorted order")
        if (
            self.primary_character_id is not None
            and self.primary_character_id not in self.selected_character_ids
        ):
            raise ValueError("the dispatch primary must belong to the dispatch selection")
        return self


class CommandDeliveryStatus(StrEnum):
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class RecipientOrderDisposition(StrEnum):
    RETAINED = "retained"
    NATURALLY_ENDED = "naturally_ended"
    REPLACED = "replaced"
    EXPLICITLY_CLEARED = "explicitly_cleared"
    RECIPIENT_UNAVAILABLE = "recipient_unavailable"
    UNKNOWN = "unknown"
    UNKNOWN_AFTER_SESSION_RESET = "unknown_after_session_reset"


class RecipientCommandState(StrictModel):
    """Command evidence for one captured recipient."""

    recipient_id: EntityId
    accepted: bool
    activity_observed: bool
    outcome_observed: bool
    current_activity: TaskEvidence | None
    disposition: RecipientOrderDisposition
    disposition_reason: str = Field(min_length=1, max_length=200)
    superseded_by_command_id: CommandId | None
    last_observed_sequence: int | None = Field(ge=0)

    @model_validator(mode="after")
    def evidence_is_causally_possible(self) -> RecipientCommandState:
        if not self.accepted and (self.activity_observed or self.outcome_observed):
            raise ValueError("unaccepted recipients cannot have activity or outcome evidence")
        if (
            self.superseded_by_command_id is not None
            and self.disposition is not RecipientOrderDisposition.REPLACED
        ):
            raise ValueError("only a replaced recipient may name a superseding command")
        if (
            self.disposition is RecipientOrderDisposition.REPLACED
            and self.superseded_by_command_id is None
        ):
            raise ValueError("a replaced recipient requires superseded_by_command_id")
        return self


class ControllerCommandRecord(StrictModel):
    """One command issued by this controller, never all Kenshi work."""

    command_id: CommandId
    identity_session_id: EntityId
    control_ownership_generation: int = Field(ge=0)
    command: str = Field(min_length=1, max_length=80)
    target_id: EntityId | None
    dispatch_basis: DispatchBasis
    delivery_status: CommandDeliveryStatus
    accepted_at_sequence: int | None = Field(ge=0)
    highest_milestone: str | None = Field(max_length=80)
    recipient_states: BoundedCollection[RecipientCommandState]
    supersedes_command_ids: list[CommandId] = Field(max_length=64)
    monitor_disposition: MonitorDisposition | None
    terminal_at_sequence: int | None = Field(ge=0)

    @model_validator(mode="after")
    def record_is_complete_and_correlated(self) -> ControllerCommandRecord:
        if self.recipient_states.completeness is not CollectionCompleteness.COMPLETE:
            raise ValueError("controller command recipient states must be complete")
        state_ids = [state.recipient_id for state in self.recipient_states.items]
        if state_ids != self.dispatch_basis.recipient_ids:
            raise ValueError(
                "recipient states must exactly match dispatch_basis.recipient_ids"
            )
        if len(self.supersedes_command_ids) != len(set(self.supersedes_command_ids)):
            raise ValueError("supersedes_command_ids must not contain duplicates")
        if self.command_id in self.supersedes_command_ids:
            raise ValueError("a command cannot supersede itself")
        if self.delivery_status is CommandDeliveryStatus.ACCEPTED:
            if self.accepted_at_sequence is None:
                raise ValueError("an accepted command requires accepted_at_sequence")
        elif self.accepted_at_sequence is not None:
            raise ValueError("only an accepted command may carry accepted_at_sequence")
        return self


class ControllerCommandRegistry(StrictModel):
    """Plural controller causality, partitioned to prevent active eviction."""

    retained_commands: BoundedCollection[ControllerCommandRecord]
    recent_terminal_commands: BoundedCollection[ControllerCommandRecord]

    @model_validator(mode="after")
    def partitions_are_disjoint_and_truthful(self) -> ControllerCommandRegistry:
        if self.retained_commands.completeness is not CollectionCompleteness.COMPLETE:
            raise ValueError("retained_commands must never be truncated")
        retained_ids = {record.command_id for record in self.retained_commands.items}
        terminal_ids = {
            record.command_id for record in self.recent_terminal_commands.items
        }
        overlap = retained_ids & terminal_ids
        if overlap:
            raise ValueError(f"command IDs occur in both registry partitions: {overlap}")
        for record in self.retained_commands.items:
            if record.terminal_at_sequence is not None:
                raise ValueError("a retained command must not carry a terminal sequence")
            if not any(
                state.disposition
                in {
                    RecipientOrderDisposition.RETAINED,
                    RecipientOrderDisposition.UNKNOWN,
                    RecipientOrderDisposition.UNKNOWN_AFTER_SESSION_RESET,
                }
                for state in record.recipient_states.items
            ):
                raise ValueError("a retained command requires at least one nonterminal recipient")
        for record in self.recent_terminal_commands.items:
            if record.terminal_at_sequence is None:
                raise ValueError("a recent terminal command requires terminal_at_sequence")
            if any(
                state.disposition is RecipientOrderDisposition.RETAINED
                for state in record.recipient_states.items
            ):
                raise ValueError("a terminal command cannot retain a recipient order")
        return self


class UnownedWorkChannel(StrEnum):
    ORDINARY_ORDER = "ordinary_order"
    JOB = "job"
    PERMANENT_JOB = "permanent_job"
    CURRENT_ACTIVITY = "current_activity"


class UnownedWorkReason(StrEnum):
    PREEXISTING = "preexisting"
    HUMAN_ISSUED = "human_issued"
    AI_ISSUED = "ai_issued"
    UNATTRIBUTED = "unattributed"
    OWNERSHIP_LOST_AFTER_SESSION_RESET = "ownership_lost_after_session_reset"


class ObservedUnownedKenshiWork(StrictModel):
    """World work observed without inventing controller ownership."""

    ownership: Literal["observed_unowned"]
    character_id: EntityId
    channel: UnownedWorkChannel
    task: TaskEvidence
    reason: UnownedWorkReason
    observed_at_sequence: int = Field(ge=0)


class Protocol2WorldModel(StrictModel):
    """The complete Protocol 2.0 world-model decision boundary."""

    protocol_version: Literal["2.0.0"]
    sequence: int = Field(ge=0)
    identity_session_id: EntityId
    roster: BoundedCollection[PlayerCharacterState]
    platoons: BoundedCollection[PlatoonState]
    active_platoon_id: EntityId | None
    primary_character_id: EntityId | None
    selected_character_ids: EntityIdSet
    controller_commands: ControllerCommandRegistry
    observed_unowned_kenshi_work: BoundedCollection[ObservedUnownedKenshiWork]

    @model_validator(mode="after")
    def identities_and_memberships_are_consistent(self) -> Protocol2WorldModel:
        roster_by_id = {character.id: character for character in self.roster.items}
        if len(roster_by_id) != len(self.roster.items):
            raise ValueError("roster character IDs must be unique")
        platoons_by_id = {platoon.id: platoon for platoon in self.platoons.items}
        if len(platoons_by_id) != len(self.platoons.items):
            raise ValueError("platoon IDs must be unique")

        member_owner: dict[str, str] = {}
        for platoon in self.platoons.items:
            for member_id in platoon.member_ids.items:
                character = roster_by_id.get(member_id)
                if character is None:
                    raise ValueError("platoon memberships must refer to roster IDs")
                if character.platoon_id != platoon.id:
                    raise ValueError("roster and platoon membership disagree")
                if member_id in member_owner:
                    raise ValueError("a roster member cannot belong to two platoons")
                member_owner[member_id] = platoon.id

        for character in self.roster.items:
            if character.platoon_id is None:
                continue
            referenced_platoon = platoons_by_id.get(character.platoon_id)
            if referenced_platoon is None:
                if self.platoons.completeness is CollectionCompleteness.COMPLETE:
                    raise ValueError("character platoon_id must refer to a listed platoon")
                continue
            if (
                referenced_platoon.member_ids.completeness
                is CollectionCompleteness.COMPLETE
                and character.id not in referenced_platoon.member_ids.items
            ):
                raise ValueError("complete platoon membership omits a roster member")

        if self.active_platoon_id is not None and self.active_platoon_id not in platoons_by_id:
            raise ValueError("active_platoon_id must refer to a listed platoon")
        selected = set(self.selected_character_ids.items)
        if not selected.issubset(roster_by_id):
            raise ValueError("selected_character_ids must refer to roster IDs")
        if (
            self.primary_character_id is not None
            and self.primary_character_id not in selected
        ):
            raise ValueError("primary_character_id must belong to the selected set")
        if self.primary_character_id is not None and self.primary_character_id not in roster_by_id:
            raise ValueError("primary_character_id must refer to the roster")

        for command in (
            *self.controller_commands.retained_commands.items,
            *self.controller_commands.recent_terminal_commands.items,
        ):
            basis_ids = {
                *command.dispatch_basis.selected_character_ids,
                *command.dispatch_basis.recipient_ids,
            }
            if command.dispatch_basis.primary_character_id is not None:
                basis_ids.add(command.dispatch_basis.primary_character_id)
            if not basis_ids.issubset(roster_by_id):
                raise ValueError("controller command identities must refer to roster IDs")
            basis_platoon = command.dispatch_basis.active_platoon_id
            if basis_platoon is not None and basis_platoon not in platoons_by_id:
                raise ValueError("command active_platoon_id must refer to a listed platoon")

        for work in self.observed_unowned_kenshi_work.items:
            if work.character_id not in roster_by_id:
                raise ValueError("unowned work must refer to a roster character")
            if work.observed_at_sequence > self.sequence:
                raise ValueError("unowned work evidence cannot postdate the snapshot")
        return self
