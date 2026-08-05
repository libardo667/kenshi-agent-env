"""How one operation addresses Kenshi: which part, whose order, for how long.

`SelectionRequirement` answered "how many characters must be selected" and was
made to stand in for six independent questions. It could not distinguish a
camera rotation from a squad order, or an order that needs the selection only
at dispatch from a UI transaction that needs it throughout. Worse, it was a
second authority: affordance enumeration never consulted it, so an operation
could sit on the planner's menu while its own definition would refuse it.

These dimensions are deliberately orthogonal. Do not collapse them back into
one enum with combined members like `PRIMARY_UI_UNTIL_COMPLETE`; the point is
that an operation answers each question separately.

The repository already uses `ExecutionScope` for correlation identity. This is
a different concept and deliberately does not reuse that name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class InteractionKind(StrEnum):
    """Which part of Kenshi an interaction addresses."""

    RUNTIME_ONLY = "runtime_only"
    """No Kenshi input and no recipient."""

    GLOBAL_UI = "global_ui"
    """Camera, playback, screens, or another game-wide UI transaction."""

    SELECTION_MUTATION = "selection_mutation"
    """Changes primary or selected set. It does not command the prior selection."""

    ORDINARY_ORDER = "ordinary_order"
    """A Kenshi order issued to characters, normally by selection broadcast."""


class RecipientScope(StrEnum):
    """Who receives the order this operation issues."""

    NONE = "none"
    """No character recipient."""

    PRIMARY = "primary"
    """Kenshi's actual primary character, exported as `ui.selected_character_id`."""

    CURRENT_SELECTION = "current_selection"
    """Every selected character at final dispatch.

    This means the exact selection that made the affordance authorable and that
    is revalidated at dispatch - never "whoever happens to be selected by the
    time input lands".
    """

    EXPLICIT_RECIPIENTS = "explicit_recipients"
    """Stable character IDs carried by the typed action or its binding."""


class SelectionDependency(StrEnum):
    """How long the UI selection must hold still.

    There is deliberately no "through monitor" value. Once an order is
    accepted, Kenshi owns it and the selection is free to change.
    """

    NONE = "none"
    DISPATCH_ONLY = "dispatch_only"
    UI_TRANSACTION = "ui_transaction"


class CompletionMilestone(StrEnum):
    """What this operation's terminal success actually claims.

    Ordered from weakest to strongest claim. An operation must not promote a
    lower milestone into a higher one: accepting an order is not arriving.
    """

    INPUT_DELIVERED = "input_delivered"
    ORDER_ACCEPTED = "order_accepted"
    ACTIVITY_RUNNING = "activity_running"
    WORLD_OUTCOME_OBSERVED = "world_outcome_observed"


class RecipientConflictPolicy(StrEnum):
    """What happens when a new order overlaps an existing one's recipients.

    There is no queueing member. Queue semantics and the exact dispatch
    modifier are unproven; add a member in the change that proves and
    implements queueing, not before.
    """

    NOT_APPLICABLE = "not_applicable"
    REJECT_OVERLAP = "reject_overlap"
    SUPERSEDE_OWNED_ORDER = "supersede_owned_order"


class PlaybackRequirement(StrEnum):
    """What simulation state this operation needs to make progress.

    A declaration, not permission to toggle global playback. Pause and speed
    affect every character and every retained order, so one runtime owner
    reconciles these; a handler or monitor must never satisfy its own.
    """

    ANY = "any"
    PAUSED_TRANSACTION = "paused_transaction"
    RUNNING_FOR_PROGRESS = "running_for_progress"


@dataclass(frozen=True, slots=True)
class OperationInteractionContract:
    """One operation's resolved answer to all six questions."""

    interaction_kind: InteractionKind
    recipient_scope: RecipientScope
    selection_dependency: SelectionDependency
    completion_milestone: CompletionMilestone
    conflict_policy: RecipientConflictPolicy
    playback_requirement: PlaybackRequirement

    def __post_init__(self) -> None:
        addresses_characters = self.interaction_kind is InteractionKind.ORDINARY_ORDER
        if addresses_characters and self.recipient_scope is RecipientScope.NONE:
            raise ValueError("an ordinary order must name a recipient scope")
        if not addresses_characters and self.conflict_policy is not (
            RecipientConflictPolicy.NOT_APPLICABLE
        ):
            raise ValueError(
                "only an ordinary order can conflict over recipients; "
                f"{self.interaction_kind.value} must use not_applicable"
            )
        if (
            self.interaction_kind is InteractionKind.RUNTIME_ONLY
            and self.recipient_scope is not RecipientScope.NONE
        ):
            raise ValueError("a runtime-only interaction has no recipients")

    @property
    def issues_world_order(self) -> bool:
        """Whether accepting this operation leaves work retained in Kenshi."""

        return self.interaction_kind is InteractionKind.ORDINARY_ORDER

    @property
    def claims_world_outcome(self) -> bool:
        """Whether terminal success asserts a world change, not just delivery."""

        return self.completion_milestone is CompletionMilestone.WORLD_OUTCOME_OBSERVED

    def fingerprint(self) -> str:
        """Stable identity of this contract, for same-operation identity checks."""

        payload = "|".join(
            (
                self.interaction_kind.value,
                self.recipient_scope.value,
                self.selection_dependency.value,
                self.completion_milestone.value,
                self.conflict_policy.value,
                self.playback_requirement.value,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def runtime_only(
    *,
    playback: PlaybackRequirement = PlaybackRequirement.ANY,
) -> OperationInteractionContract:
    """Run control and cognition: no Kenshi input, no recipient."""

    return OperationInteractionContract(
        interaction_kind=InteractionKind.RUNTIME_ONLY,
        recipient_scope=RecipientScope.NONE,
        selection_dependency=SelectionDependency.NONE,
        completion_milestone=CompletionMilestone.INPUT_DELIVERED,
        conflict_policy=RecipientConflictPolicy.NOT_APPLICABLE,
        playback_requirement=playback,
    )


def global_ui(
    *,
    milestone: CompletionMilestone = CompletionMilestone.INPUT_DELIVERED,
    selection: SelectionDependency = SelectionDependency.NONE,
    recipients: RecipientScope = RecipientScope.NONE,
    playback: PlaybackRequirement = PlaybackRequirement.ANY,
) -> OperationInteractionContract:
    """Camera, playback, screens, and window-owning transactions."""

    return OperationInteractionContract(
        interaction_kind=InteractionKind.GLOBAL_UI,
        recipient_scope=recipients,
        selection_dependency=selection,
        completion_milestone=milestone,
        conflict_policy=RecipientConflictPolicy.NOT_APPLICABLE,
        playback_requirement=playback,
    )


def selection_mutation() -> OperationInteractionContract:
    """Changing who is selected, which is not an order to anybody."""

    return OperationInteractionContract(
        interaction_kind=InteractionKind.SELECTION_MUTATION,
        recipient_scope=RecipientScope.EXPLICIT_RECIPIENTS,
        selection_dependency=SelectionDependency.NONE,
        completion_milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
        conflict_policy=RecipientConflictPolicy.NOT_APPLICABLE,
        playback_requirement=PlaybackRequirement.ANY,
    )


def ordinary_order(
    *,
    recipients: RecipientScope,
    milestone: CompletionMilestone,
    selection: SelectionDependency = SelectionDependency.DISPATCH_ONLY,
    conflict: RecipientConflictPolicy = RecipientConflictPolicy.SUPERSEDE_OWNED_ORDER,
    playback: PlaybackRequirement = PlaybackRequirement.RUNNING_FOR_PROGRESS,
) -> OperationInteractionContract:
    """An order issued to characters that Kenshi retains and carries out."""

    return OperationInteractionContract(
        interaction_kind=InteractionKind.ORDINARY_ORDER,
        recipient_scope=recipients,
        selection_dependency=selection,
        completion_milestone=milestone,
        conflict_policy=conflict,
        playback_requirement=playback,
    )
