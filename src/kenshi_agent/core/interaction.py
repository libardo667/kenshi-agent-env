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
import json
from collections.abc import Sequence
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
    """Kenshi's actual primary character, exported as `primary_character_id`."""

    CURRENT_SELECTION = "current_selection"
    """Every selected character at final dispatch.

    This means the exact selection that made the affordance authorable and that
    is revalidated at dispatch - never "whoever happens to be selected by the
    time input lands".
    """

    EXPLICIT_RECIPIENTS = "explicit_recipients"
    """Stable character IDs carried by the typed action or its binding."""

    NAMED_BODY = "named_body"
    """One character the action names, who need not be the agent's at all.

    Every other scope presupposes a living roster or selection to command. This
    one exists because the case it serves is the absence of both: when every
    character is dead the squad is empty, nothing is selected, and the only
    operation still worth issuing is the one that takes a body which was never
    yours. Requiring recipients there would make the recovery unreachable at
    exactly the moment it is the whole point.
    """


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


# Action fields that name explicit character recipients. EXPLICIT_RECIPIENTS
# operations carry who they act on in the typed action itself, so the basis is
# read from the action rather than from whatever is selected.
_EXPLICIT_RECIPIENT_FIELDS: tuple[str, ...] = (
    "actor_id",
    "character_id",
    "owner_id",
    "recipient_ids",
    "squad_member_id",
)


@dataclass(frozen=True, slots=True)
class AuthoredRecipientBasis:
    """Who an operation was authored to command, captured when it was authored.

    The seam this closes: a planner authors an order while A and B are selected,
    the operation waits an unbounded interval for the input lease, selection
    becomes C, and rebinding reads the current selection - so an order authored
    for A+B is delivered to C. Operation identity covered the definition, the
    typed action, the affordance, and the binding, and none of those mention who
    acts, so the substitution changed no fingerprint and raised no objection.

    Only the fields the scope actually uses take part in identity. A survey is
    `RecipientScope.NONE` and commands nobody, so a selection change must not
    invalidate it; a `PRIMARY` order does not care how many others are selected
    alongside its primary. Comparing more than the scope uses would manufacture
    staleness, which is its own way of being wrong.
    """

    scope: RecipientScope
    # Kenshi's exported `primary_character_id` - never whichever
    # selected roster member happens to sort or arrive first.
    primary: str | None = None
    # Sorted, because selection is a set: the same two characters selected in a
    # different order is the same basis.
    selection: tuple[str, ...] = ()
    explicit_recipients: tuple[str, ...] = ()

    @classmethod
    def capture(
        cls,
        scope: RecipientScope,
        *,
        primary: str | None,
        selection: Sequence[str],
        explicit_recipients: Sequence[str] = (),
    ) -> AuthoredRecipientBasis:
        return cls(
            scope=scope,
            primary=primary,
            selection=tuple(sorted(selection)),
            explicit_recipients=tuple(sorted(explicit_recipients)),
        )

    @property
    def identity_fields(self) -> dict[str, object]:
        """Exactly the fields this scope commands with."""

        if self.scope is RecipientScope.NONE:
            return {"scope": self.scope.value}
        if self.scope is RecipientScope.PRIMARY:
            return {"scope": self.scope.value, "primary": self.primary}
        if self.scope is RecipientScope.CURRENT_SELECTION:
            return {
                "scope": self.scope.value,
                "primary": self.primary,
                "selection": list(self.selection),
            }
        return {
            "scope": self.scope.value,
            "explicit_recipients": list(self.explicit_recipients),
        }

    def fingerprint(self) -> str:
        payload = json.dumps(self.identity_fields, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def differences_from(self, other: AuthoredRecipientBasis) -> tuple[str, ...]:
        """What changed, in terms a post-mortem can act on.

        A bare fingerprint mismatch says an operation went stale without saying
        who it would have commanded instead, which is the one fact needed to
        tell a real recipient substitution from an unrelated churn.
        """

        if other.scope is not self.scope:
            return (f"recipient scope changed from {self.scope.value} to {other.scope.value}",)
        changes: list[str] = []
        mine, theirs = self.identity_fields, other.identity_fields
        if "primary" in mine and mine["primary"] != theirs["primary"]:
            changes.append(f"primary changed from {mine['primary']!r} to {theirs['primary']!r}")
        if "selection" in mine and mine["selection"] != theirs["selection"]:
            changes.append(
                f"selection changed from {mine['selection']} to {theirs['selection']}"
            )
        if "explicit_recipients" in mine and (
            mine["explicit_recipients"] != theirs["explicit_recipients"]
        ):
            changes.append(
                f"explicit recipients changed from {mine['explicit_recipients']} "
                f"to {theirs['explicit_recipients']}"
            )
        return tuple(changes)

    def matches(self, other: AuthoredRecipientBasis) -> bool:
        return not self.differences_from(other)


def explicit_recipients_of(action: object) -> tuple[str, ...]:
    """Character IDs an action names directly, if any."""

    found: list[str] = []
    for name in _EXPLICIT_RECIPIENT_FIELDS:
        value = getattr(action, name, None)
        if isinstance(value, str) and value:
            found.append(value)
        elif isinstance(value, (list, tuple)):
            found.extend(str(item) for item in value if item)
    return tuple(sorted(set(found)))
