"""An order authored for A and B must never be delivered to C.

The seam: a planner authors an order while A and B are selected. The operation
then waits an unbounded interval for the input lease. Selection becomes C.
Rebinding reads the *current* selection, so the order is delivered to C.

Operation identity covered the definition, the typed action, the affordance and
the binding. None of those mention who acts, so the substitution changed no
fingerprint and raised no objection anywhere between authoring and dispatch.

These tests are deterministic - no live game, no timing - and each one proves
that the refusal happened before any primitive was delivered.
"""

from __future__ import annotations

import pytest

from kenshi_agent.core.authority import AuthorizationCode
from kenshi_agent.core.interaction import (
    AuthoredRecipientBasis,
    RecipientScope,
    explicit_recipients_of,
)
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import ControlMode, PerformContextAction
from kenshi_agent.core.telemetry import (
    CharacterState,
    ContextActionKind,
    GameState,
    TelemetrySnapshot,
    UIState,
    Vec3,
    WorldTarget,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_definitions import OPERATION_DEFINITIONS

A, B, C = "char-a", "char-b", "char-c"
IRON = "entity-iron"


class RecordingInput:
    """Stands in for the host input surface, and remembers being touched.

    "No input was delivered" is the claim that actually matters; asserting only
    on a refusal code would pass just as well if the refusal arrived after the
    keystrokes did.
    """

    def __init__(self) -> None:
        self.primitives: list[str] = []

    def send(self, primitive: str) -> None:  # pragma: no cover - must never run
        self.primitives.append(primitive)


def _observation(*, primary: str | None, selected: list[str], sequence: int = 10) -> Observation:
    roster = [
        CharacterState(id=identity, name=identity.upper())
        for identity in (A, B, C)
    ]
    return Observation(
        run_id="recipient-identity-test",
        step_index=1,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(telemetry_sequence=sequence, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            identity_session_id="recipient-session",
            capabilities=sorted(
                OPERATION_DEFINITIONS["perform_context_action"].required_capabilities
                | {"game.pause", "roster.basic"}
            ),
            game=GameState(loaded=True, paused=False),
            primary_character_id=primary,
            selected_character_ids=selected,
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
            ),
            roster=roster,
            world_targets=[
                WorldTarget(
                    id=IRON,
                    name="Iron Resource",
                    kind="natural_resource",
                    position=Vec3(x=0.0, y=0.0, z=0.0),
                    distance=12.0,
                    default_task="TASK_MINE",
                    context_actions=[ContextActionKind.OPERATE],
                )
            ],
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.1,
    )


def _mining_action() -> PerformContextAction:
    return PerformContextAction(target_id=IRON, context_action=ContextActionKind.OPERATE)


def _authority():  # type: ignore[no-untyped-def]
    from kenshi_agent.affordances import OPERATION_BINDING_AUTHORITY
    from kenshi_agent.config import SafetyConfig
    from kenshi_agent.operation_authority import OperationAuthority
    from kenshi_agent.safety import OperationPolicy

    policy = OperationPolicy(
        SafetyConfig(
            allow_action_kinds=["perform_context_action"],
            max_wait_seconds=3.0,
            max_actions_per_minute=100,
        ),
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    return OperationAuthority(policy, OPERATION_BINDING_AUTHORITY)


def _bind(action: PerformContextAction, observation: Observation):  # type: ignore[no-untyped-def]
    from kenshi_agent.affordances import OPERATION_BINDING_AUTHORITY

    return OPERATION_BINDING_AUTHORITY.bind(action, observation, affordance=None)


# --------------------------------------------------------------------------
# The basis itself


def test_the_authored_basis_records_kenshis_primary_not_roster_order() -> None:
    """B is selected first in the list; A is Kenshi's exported primary."""

    observation = _observation(primary=A, selected=[B, A])

    bound = _bind(_mining_action(), observation)
    basis = bound.identity.recipient_basis

    assert basis is not None
    assert basis.primary == A
    assert basis.selection == (A, B)


def test_the_same_two_characters_in_another_order_are_the_same_basis() -> None:
    """Selection is a set. Reordering it is not a recipient change."""

    first = _bind(_mining_action(), _observation(primary=A, selected=[A, B]))
    second = _bind(_mining_action(), _observation(primary=A, selected=[B, A], sequence=11))

    assert first.identity.recipient_basis is not None
    assert second.identity.recipient_basis is not None
    assert first.identity.recipient_basis.matches(second.identity.recipient_basis)


def test_a_scope_that_commands_nobody_is_untouched_by_selection_change() -> None:
    """A survey reads the world. Selection churn must not make it stale."""

    authored = AuthoredRecipientBasis.capture(
        RecipientScope.NONE, primary=A, selection=[A, B]
    )
    later = AuthoredRecipientBasis.capture(RecipientScope.NONE, primary=C, selection=[C])

    assert authored.matches(later)


# --------------------------------------------------------------------------
# The seam: A+B becomes C during the lease wait


def test_a_plus_b_becoming_c_during_the_lease_is_refused_and_sends_nothing() -> None:
    """The exact substitution this closes."""

    host = RecordingInput()
    authored_at = _observation(primary=A, selected=[A, B])
    bound = _bind(_mining_action(), authored_at)

    # The lease wait happens here. Selection becomes C.
    at_dispatch = _observation(primary=C, selected=[C], sequence=42)

    decision = _authority().evaluate(bound, at_dispatch)

    assert not decision.allowed
    assert decision.code is AuthorizationCode.STALE_RECIPIENT_BASIS
    assert host.primitives == []


def test_the_refusal_names_who_it_would_have_commanded_instead() -> None:
    """A bare fingerprint mismatch cannot be acted on; this can."""

    bound = _bind(_mining_action(), _observation(primary=A, selected=[A, B]))

    decision = _authority().evaluate(
        bound, _observation(primary=C, selected=[C], sequence=42)
    )

    details = decision.details
    assert details["authored_primary"] == A
    assert details["authored_selection"] == [A, B]
    assert details["current_primary"] == C
    assert details["current_selection"] == [C]
    assert "authored for different recipients" in str(details["violation"])


def test_losing_one_of_two_recipients_is_still_a_changed_order() -> None:
    """A+B is not A. Half an order is not the order that was authored."""

    host = RecordingInput()
    bound = _bind(_mining_action(), _observation(primary=A, selected=[A, B]))

    decision = _authority().evaluate(
        bound, _observation(primary=A, selected=[A], sequence=42)
    )

    assert not decision.allowed
    assert decision.code is AuthorizationCode.STALE_RECIPIENT_BASIS
    assert host.primitives == []


def test_the_primary_moving_within_the_same_selection_is_refused() -> None:
    """Same two characters, different primary: PRIMARY-scoped work would move."""

    host = RecordingInput()
    bound = _bind(_mining_action(), _observation(primary=A, selected=[A, B]))

    decision = _authority().evaluate(
        bound, _observation(primary=B, selected=[A, B], sequence=42)
    )

    assert not decision.allowed
    assert decision.code is AuthorizationCode.STALE_RECIPIENT_BASIS
    assert "primary changed" in str(decision.details["violation"])
    assert host.primitives == []


def test_an_unchanged_basis_is_allowed_through() -> None:
    """The refusal must not be a blanket one, or it proves nothing."""

    bound = _bind(_mining_action(), _observation(primary=A, selected=[A, B]))

    decision = _authority().evaluate(
        bound, _observation(primary=A, selected=[A, B], sequence=42)
    )

    assert decision.allowed, decision.details
    assert decision.code is AuthorizationCode.ALLOWED


# --------------------------------------------------------------------------
# Explicit recipients


def test_explicit_recipients_are_read_from_the_action_not_the_selection() -> None:
    from kenshi_agent.core.operation import RegroupWithSquadMemberAction

    action = RegroupWithSquadMemberAction(actor_id=A, target_id=B)

    assert A in explicit_recipients_of(action)


def test_an_explicit_recipient_change_is_a_different_order() -> None:
    authored = AuthoredRecipientBasis.capture(
        RecipientScope.EXPLICIT_RECIPIENTS, primary=None, selection=(), explicit_recipients=[A]
    )
    later = AuthoredRecipientBasis.capture(
        RecipientScope.EXPLICIT_RECIPIENTS, primary=None, selection=(), explicit_recipients=[C]
    )

    changes = authored.differences_from(later)

    assert changes
    assert "explicit recipients changed" in changes[0]


def test_explicit_recipients_ignore_selection_churn() -> None:
    """Who is selected is irrelevant when the action names its own recipients."""

    authored = AuthoredRecipientBasis.capture(
        RecipientScope.EXPLICIT_RECIPIENTS,
        primary=A,
        selection=[A, B],
        explicit_recipients=[A],
    )
    later = AuthoredRecipientBasis.capture(
        RecipientScope.EXPLICIT_RECIPIENTS,
        primary=C,
        selection=[C],
        explicit_recipients=[A],
    )

    assert authored.matches(later)


# --------------------------------------------------------------------------
# Ordering: a more specific refusal must not be hidden behind this one


def test_an_unusable_selection_is_still_reported_as_a_selection_problem() -> None:
    """Empty selection cannot satisfy the scope at all.

    Reporting that as a changed recipient basis would replace the specific
    answer with a vaguer one.
    """

    bound = _bind(_mining_action(), _observation(primary=A, selected=[A, B]))

    decision = _authority().evaluate(
        bound, _observation(primary=None, selected=[], sequence=42)
    )

    assert not decision.allowed
    assert decision.code is not AuthorizationCode.STALE_RECIPIENT_BASIS


@pytest.mark.parametrize("scope", list(RecipientScope))
def test_every_scope_has_defined_identity_fields(scope: RecipientScope) -> None:
    """Guards against a new scope silently comparing nothing."""

    basis = AuthoredRecipientBasis.capture(scope, primary=A, selection=[A, B])

    assert basis.identity_fields["scope"] == scope.value
    assert basis.fingerprint()


# --------------------------------------------------------------------------
# "The selected character" is Kenshi's primary, not roster order


def _condition_subject(primary: str | None, selected: list[str]):  # type: ignore[no-untyped-def]
    from kenshi_agent.condition_evaluation import _selected_character

    return _selected_character(_observation(primary=primary, selected=selected))


def test_the_exported_primary_wins_over_roster_order() -> None:
    """B sorts first in the roster; A is the primary."""

    subject = _condition_subject(A, [A, B])

    assert subject is not None
    assert subject.id == A


def test_an_ambiguous_selection_without_a_primary_resolves_to_nothing() -> None:
    """Two selected and no exported primary is unknown, not "the first one".

    This used to return the first `selected` roster member, so a fact about
    "the selected character" was answered about whoever the exporter happened
    to walk first.
    """

    assert _condition_subject(None, [A, B]) is None


def test_one_selected_character_may_stand_in_for_an_absent_primary() -> None:
    """Unambiguous is not a guess."""

    subject = _condition_subject(None, [B])

    assert subject is not None
    assert subject.id == B


def test_an_empty_selection_never_borrows_the_first_roster_member() -> None:
    """The worst of the old fallbacks: `squad[0]`, selected or not."""

    assert _condition_subject(None, []) is None


# --------------------------------------------------------------------------
# Affordance identity is what the choice is, not when it was seen


def test_the_same_choice_keeps_its_identity_across_telemetry_publications() -> None:
    """A lease wait that spanned a publication made the choice unauthorizable.

    Observed live: the planner authored `operate` on an iron deposit at
    telemetry 244, the input lease waited, telemetry advanced to 275, and the
    boundary refused with "Affordance is absent from the current observation" -
    while the deposit was still there, both characters were still selected, and
    the same choice was being offered under a new identity.

    Paused-world UI actions kept succeeding because their waits did not span a
    publication, which is why this looked like a mining-specific problem.
    """

    from kenshi_agent.affordances import _offer_id
    from kenshi_agent.core.affordance import AffordanceSource

    choice = {
        "source": AffordanceSource.CONTEXT_ORDER,
        "semantic": "operate",
        "target_id": "entity-iron",
        "operation_kind": "perform_context_action",
        "operation_arguments": {},
    }

    assert _offer_id(sequence=244, **choice) == _offer_id(sequence=275, **choice)


def test_identity_still_separates_genuinely_different_choices() -> None:
    """Stability must not become collision, or the fix is worse than the bug."""

    from kenshi_agent.affordances import _offer_id
    from kenshi_agent.core.affordance import AffordanceSource

    base = {
        "sequence": 1,
        "source": AffordanceSource.CONTEXT_ORDER,
        "semantic": "operate",
        "target_id": "entity-iron",
        "operation_kind": "perform_context_action",
        "operation_arguments": {},
    }
    identity = _offer_id(**base)

    assert _offer_id(**{**base, "target_id": "entity-copper"}) != identity
    assert _offer_id(**{**base, "semantic": "survey_local_resources"}) != identity
    assert _offer_id(**{**base, "operation_kind": "produce_resource_output"}) != identity
    assert _offer_id(**{**base, "operation_arguments": {"quantity": 2}}) != identity
    assert _offer_id(**{**base, "source": AffordanceSource.RUNTIME}) != identity


def test_when_the_offer_was_made_is_still_recorded() -> None:
    """Provenance is kept as a field rather than smuggled into identity."""

    from kenshi_agent.core.affordance import BoundAffordance

    assert "offered_at_telemetry_sequence" in BoundAffordance.model_fields
