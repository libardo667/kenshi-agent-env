"""Every operation must be reachable, by an adapter or by composition.

The registry audit checked one direction: adapters naming definitions that do
not exist. Nothing checked the reverse, so an operation could have a definition,
a binder, a handler and a passing audit while no adapter would ever offer it.

Six were in that state. Three of them - `pause`, `set_speed` and `wait` - were
the whole playback surface, which meant an agent handed a paused Kenshi save
could not start the clock. A live run proved it: sixty-four affordances on the
menu, three consecutive world commands, "no causal transition" each time, and
elapsed_minutes frozen at its starting value for all 158 observations.
"""

from __future__ import annotations

from kenshi_agent.affordances import AFFORDANCE_ADAPTERS
from kenshi_agent.operation_definitions import OPERATION_DEFINITIONS
from kenshi_agent.tooling.operation_registry_audit import (
    REACHED_BY_COMPOSITION,
    audit_operation_registry,
)


def test_no_operation_is_built_and_unofferable() -> None:
    audit = audit_operation_registry()

    assert audit.unreachable_definitions == (), (
        "These operations have a definition and a handler but no adapter offers "
        f"them, so the agent can never choose them: {audit.unreachable_definitions}"
    )


def test_the_probe_would_notice_an_unreachable_operation() -> None:
    """Guards against the check passing because it inspects nothing."""

    adapter_kinds = {
        kind for adapter in AFFORDANCE_ADAPTERS for kind in adapter.operation_kinds
    }

    assert OPERATION_DEFINITIONS.keys() - adapter_kinds == REACHED_BY_COMPOSITION


def test_playback_control_is_reachable() -> None:
    """The specific gap the live run found."""

    adapter_kinds = {
        kind for adapter in AFFORDANCE_ADAPTERS for kind in adapter.operation_kinds
    }

    assert {"pause", "set_speed", "wait"} <= adapter_kinds


def test_composition_claims_name_real_operations() -> None:
    """A name here asserts some offered operation runs it; it must at least exist."""

    assert REACHED_BY_COMPOSITION <= OPERATION_DEFINITIONS.keys()


def _paused_observation(*, paused: bool):
    from kenshi_agent.core.observation import Observation
    from kenshi_agent.core.telemetry import (
        CharacterState,
        GameState,
        TelemetrySnapshot,
        UIState,
    )

    return Observation(
        run_id="r",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            sequence=1,
            game=GameState(loaded=True, paused=paused, money=100),
            ui=UIState(active_screen="world", modal_open=False, dialogue_open=False),
            roster=[CharacterState(id="barth", name="Barth")],
        ),
    )


def _semantics(observation) -> set[str]:  # type: ignore[no-untyped-def]
    from kenshi_agent.affordances import offered_affordances

    return {offer.semantic for offer in offered_affordances(observation)}


def test_a_paused_world_always_offers_a_way_to_start_the_clock() -> None:
    """The exact live failure: a paused save with no move that can succeed."""

    semantics = _semantics(_paused_observation(paused=True))

    assert "resume_game" in semantics


def test_a_running_world_offers_pause_speed_and_wait_instead() -> None:
    semantics = _semantics(_paused_observation(paused=False))

    assert "pause_game" in semantics
    assert "set_game_speed" in semantics
    assert "wait" in semantics
    # Resuming an already-running world is not a choice worth offering.
    assert "resume_game" not in semantics


def test_a_modal_cannot_strand_the_agent_in_a_stopped_world() -> None:
    """Playback is declared global_ui, so a screen must not gate it.

    Interface-clear gating plus a paused world is the doubly-stuck case: no
    world command can complete, and the one operation that would fix that is
    filtered out for the duration of whatever is on screen.
    """

    from kenshi_agent.core.observation import Observation
    from kenshi_agent.core.telemetry import (
        CharacterState,
        GameState,
        TelemetrySnapshot,
        UIState,
    )

    observation = Observation(
        run_id="r",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            sequence=1,
            game=GameState(loaded=True, paused=True, money=100),
            ui=UIState(active_screen="inventory", modal_open=True, dialogue_open=False),
            roster=[CharacterState(id="barth", name="Barth")],
        ),
    )

    assert "resume_game" in _semantics(observation)


def test_a_paused_world_failure_names_the_pause_not_the_check() -> None:
    """"No causal transition" names the check that failed, not the cause.

    The cause was in all 158 observations of the bundle - the game was paused
    and elapsed_minutes never moved - while the message sent the reader to the
    handler instead.
    """

    from kenshi_agent.execution.kernel import _no_causal_transition_reason

    paused = _no_causal_transition_reason(_paused_observation(paused=True))
    running = _no_causal_transition_reason(_paused_observation(paused=False))

    assert "paused" in paused
    assert "Resume play" in paused
    assert paused != running
