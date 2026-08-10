"""Orders the agent may issue at a person, as Kenshi itself decides them.

Before this, the agent's entire combat vocabulary was one reactive operation:
`respond_to_immediate_threat`, which only exists once something is already
coming at it. It could not take a bounty, pick a fight, or loot anything it
survived -- and the reason was not a missing gate but a missing verb.

The fix is not a verb per action. Kenshi builds its own context menu for a
target, and the plug-in has it build that menu with the renderer muted, so
eligibility is the engine's judgment -- the same list a player sees on
right-click -- arriving as `advertised_tasks`. These tests pin that the offers
are whatever Kenshi said yes to, that silence is never read as permission, and
that two orders on one person stay distinguishable.
"""

from __future__ import annotations

import pytest

from kenshi_agent.affordances import offered_affordances
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import ControlMode, PerformCharacterOrderAction
from kenshi_agent.core.telemetry import (
    AdvertisedTask,
    AdvertisedTaskSource,
    CharacterState,
    Disposition,
    GameState,
    NearbyEntity,
    TelemetrySnapshot,
    UIState,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_definitions import bind_perform_character_order

MENU = AdvertisedTaskSource.MENU
ATTACK = AdvertisedTask(value=9, name="CHOOSE_ENEMY_AND_ATTACK", source=MENU)
LOOT = AdvertisedTask(value=26, name="LOOT_TARGET", source=MENU)
AID = AdvertisedTask(value=25, name="FIRST_AID_ORDER", source=MENU)


def _person(
    entity_id: str,
    name: str,
    *,
    tasks: list[AdvertisedTask] | None = None,
    probed: bool = True,
    disposition: Disposition = Disposition.HOSTILE,
    conscious: bool = True,
) -> NearbyEntity:
    return NearbyEntity(
        id=entity_id,
        name=name,
        kind="character",
        is_animal=False,
        has_dialogue=False,
        disposition=disposition,
        conscious=conscious,
        distance=8.0,
        faction="Dust Bandits",
        advertised_tasks=list(tasks or []),
        advertised_tasks_probed=probed,
    )


def _world(*people: NearbyEntity) -> Observation:
    return Observation(
        run_id="character-orders",
        step_index=1,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(telemetry_sequence=11, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=11,
            identity_session_id="orders-session",
            capabilities=[
                "control.perform_character_order",
                "game.pause",
                "identity.stable_handles",
                "nearby.characters",
                "nearby.orderable_tasks",
                "roster.basic",
            ],
            game=GameState(loaded=True, paused=True),
            primary_character_id="char-leaf",
            selected_character_ids=["char-leaf"],
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
            ),
            roster=[
                CharacterState(
                    id="char-leaf",
                    name="LEAF",
                    alive=True,
                    conscious=True,
                )
            ],
            nearby_entities=list(people),
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.05,
    )


def _semantics(observation: Observation) -> set[str]:
    return {
        offer.semantic
        for offer in offered_affordances(observation)
        if offer.operation_kind == "perform_character_order"
    }


def test_a_hostile_is_orderable_even_though_it_is_not_talkable() -> None:
    """The hole this closes: approach refuses hostiles, and nothing else took them.

    A bandit has no dialogue and a hostile disposition, so every talk-shaped
    path excludes it by design. An order is not a conversation.
    """

    observation = _world(_person("e-bandit", "Hungry bandit", tasks=[ATTACK]))

    assert _semantics(observation) == {"choose_enemy_and_attack"}


def test_the_menu_is_whatever_kenshi_advertised_and_nothing_else() -> None:
    """No curated verb list: two people, different answers, both honoured."""

    observation = _world(
        _person("e-bandit", "Hungry bandit", tasks=[ATTACK]),
        _person(
            "e-corpse",
            "Dead bandit",
            tasks=[LOOT],
            conscious=False,
        ),
    )

    assert _semantics(observation) == {"choose_enemy_and_attack", "loot_target"}


def test_two_orders_on_one_person_stay_separately_choosable() -> None:
    """The identity that mattered: (semantic, target_id), not target alone.

    A downed bandit affording both looting and a finishing blow must be two
    choices, not one choice that silently resolves to whichever was enumerated
    first. Offer identity collapsing on a duplicate is exactly the failure that
    once looked like a hallucinated affordance id.
    """

    observation = _world(
        _person("e-downed", "Downed bandit", tasks=[LOOT, ATTACK], conscious=False)
    )

    offers = [
        offer
        for offer in offered_affordances(observation)
        if offer.operation_kind == "perform_character_order"
    ]

    assert {offer.semantic for offer in offers} == {"loot_target", "choose_enemy_and_attack"}
    assert len({offer.affordance_id for offer in offers}) == 2


def test_an_unprobed_person_is_offered_nothing_rather_than_everything() -> None:
    """Silence is not a denial, and it is not permission either."""

    observation = _world(
        _person("e-far", "Distant bandit", tasks=[], probed=False),
    )

    assert _semantics(observation) == set()


def test_an_unprobed_person_cannot_be_ordered_at() -> None:
    """The binding fails closed on the same silence the offer stayed quiet about."""

    observation = _world(_person("e-far", "Distant bandit", tasks=[], probed=False))

    result = bind_perform_character_order(
        PerformCharacterOrderAction(target_id="e-far", order="choose_enemy_and_attack"),
        observation,
    )

    assert result.bound is False
    assert "not probed" in result.reason


def test_an_unadvertised_order_is_refused_and_names_what_is_offered() -> None:
    """A refusal that lists the real options is a refusal the planner can use.

    `selection_mismatch` once stood for six distinct conditions and cost a day
    of live diagnosis. A rejection here says which order was asked for, on whom,
    and what that person actually affords.
    """

    observation = _world(_person("e-bandit", "Hungry bandit", tasks=[ATTACK]))

    result = bind_perform_character_order(
        PerformCharacterOrderAction(target_id="e-bandit", order="loot_target"),
        observation,
    )

    assert result.bound is False
    assert "loot_target" in result.reason
    assert "choose_enemy_and_attack" in result.reason


def test_an_advertised_order_binds_to_the_exact_person() -> None:
    observation = _world(
        _person("e-bandit", "Hungry bandit", tasks=[ATTACK]),
        _person("e-other", "Second bandit", tasks=[ATTACK]),
    )

    result = bind_perform_character_order(
        PerformCharacterOrderAction(target_id="e-other", order="choose_enemy_and_attack"),
        observation,
    )

    assert result.bound is True
    assert result.target_id == "e-other"
    assert "Second bandit" in result.reason


def test_an_absent_person_fails_closed() -> None:
    observation = _world(_person("e-bandit", "Hungry bandit", tasks=[ATTACK]))

    result = bind_perform_character_order(
        PerformCharacterOrderAction(target_id="e-ghost", order="choose_enemy_and_attack"),
        observation,
    )

    assert result.bound is False
    assert "not a currently observed nearby character" in result.reason


@pytest.mark.parametrize("order", ["first_aid_order", "loot_target"])
def test_non_combat_orders_travel_the_same_path(order: str) -> None:
    """Nothing here is combat-specific; combat was only the missing case."""

    observation = _world(
        _person(
            "e-friend",
            "Wounded drifter",
            tasks=[AID, LOOT],
            disposition=Disposition.FRIENDLY,
            conscious=False,
        )
    )

    result = bind_perform_character_order(
        PerformCharacterOrderAction(target_id="e-friend", order=order),
        observation,
    )

    assert result.bound is True


def test_the_menu_authority_for_an_order_reaches_the_receipt() -> None:
    """A bound order records that the real menu path vouched for it."""

    observation = _world(
        _person(
            "e-bandit",
            "Hungry bandit",
            tasks=[AdvertisedTask(value=9, name="CHOOSE_ENEMY_AND_ATTACK", source=MENU)],
        )
    )

    attack = bind_perform_character_order(
        PerformCharacterOrderAction(target_id="e-bandit", order="choose_enemy_and_attack"),
        observation,
    )
    assert attack.bound is True
    assert "evidence: menu" in attack.reason


def test_probability_only_compatibility_source_is_rejected() -> None:
    """A retired probability result cannot re-enter through old telemetry."""

    with pytest.raises(ValueError):
        AdvertisedTask(value=125, name="KIDNAP_ORDER", source="odds")


def test_an_order_kenshi_never_advertised_has_no_evidence() -> None:
    """`order_evidence` is empty for what was never offered, not defaulted."""

    entity = _person("e-bandit", "Hungry bandit", tasks=[ATTACK])

    assert entity.order_evidence("choose_enemy_and_attack") == frozenset({MENU})
    assert entity.order_evidence("loot_target") == frozenset()


def test_the_order_name_reaching_the_wire_is_the_one_telemetry_published() -> None:
    """No translation layer: the semantic, the argument, and the task agree."""

    entity = _person("e-bandit", "Hungry bandit", tasks=[ATTACK])
    observation = _world(entity)

    offer = next(
        o for o in offered_affordances(observation) if o.operation_kind == "perform_character_order"
    )

    assert offer.semantic == "choose_enemy_and_attack"
    assert offer.operation_arguments["order"] == "choose_enemy_and_attack"
    assert offer.operation_arguments["target_id"] == "e-bandit"
    assert entity.orderable_task_names() == ("choose_enemy_and_attack",)
