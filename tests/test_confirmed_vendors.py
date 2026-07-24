"""Deterministic confirmed-vendor selection.

Whether a nearby entity is an approachable vendor is a fact the telemetry
already carries. The model kept re-deriving it by hand and getting it wrong
half the time, so the judgment lives in one deterministic place instead. These
cases use the exact Hub scene that flaked live: a Barman among Ninja Guards,
Mercenaries, and Holy Citizens.
"""

from __future__ import annotations

from kenshi_agent.models import (
    Disposition,
    NearbyEntity,
    confirmed_vendor_candidates,
    dialogue_targets,
)


def entity(name: str, **flags: object) -> NearbyEntity:
    base: dict[str, object] = {
        "id": f"entity-{name.lower().replace(' ', '-')}",
        "name": name,
    }
    base.update(flags)
    return NearbyEntity(**base)  # type: ignore[arg-type]


def barman(**overrides: object) -> NearbyEntity:
    flags: dict[str, object] = {
        "is_animal": False,
        "has_vendor_list": True,
        "is_squad_leader": True,
        "has_dialogue": True,
        "disposition": Disposition.NEUTRAL,
        "distance": 26.0,
    }
    flags.update(overrides)
    return entity("Barman", **flags)


def hub_scene() -> list[NearbyEntity]:
    return [
        barman(distance=26.0),
        # Ninja Guards carry a vendor list but are not leaders and have no dialogue.
        entity("Ninja Guard", is_animal=False, has_vendor_list=True,
               is_squad_leader=False, has_dialogue=False,
               disposition=Disposition.NEUTRAL, distance=40.0),
        # Mercenary Captain leads and talks but sells nothing -- talkable, not a vendor.
        entity("Mercenary Captain", is_animal=False, has_vendor_list=False,
               is_squad_leader=True, has_dialogue=True,
               disposition=Disposition.NEUTRAL, distance=15.0),
        entity("Mercenary", is_animal=False, has_vendor_list=False,
               is_squad_leader=False, has_dialogue=False,
               disposition=Disposition.NEUTRAL, distance=12.0),
    ]


def test_the_hub_scene_confirms_exactly_the_barman() -> None:
    vendors = confirmed_vendor_candidates(hub_scene())
    assert [v.name for v in vendors] == ["Barman"]


def test_dialogue_targets_are_broader_than_vendors() -> None:
    # The general primitive: anyone non-hostile the agent could walk up and talk
    # to -- the Mercenary Captain counts even though he sells nothing. Nearest
    # first, so the Captain (15) precedes the Barman (26).
    talkable = dialogue_targets(hub_scene())
    assert [t.name for t in talkable] == ["Mercenary Captain", "Barman"]


def test_confirmed_vendor_implies_dialogue_target() -> None:
    b = barman()
    assert b.is_dialogue_target() is True
    assert b.is_confirmed_vendor() is True


def test_a_talkable_non_vendor_is_a_dialogue_target_but_not_a_vendor() -> None:
    captain = entity("Mercenary Captain", is_animal=False, has_vendor_list=False,
                     is_squad_leader=True, has_dialogue=True, disposition=Disposition.NEUTRAL)
    assert captain.is_dialogue_target() is True
    assert captain.is_confirmed_vendor() is False


def test_no_dialogue_means_not_a_talk_target_even_with_a_vendor_list() -> None:
    # A guard carrying an inherited vendor list but no dialogue is not talkable.
    guard = entity("Ninja Guard", is_animal=False, has_vendor_list=True,
                   is_squad_leader=False, has_dialogue=False, disposition=Disposition.NEUTRAL)
    assert guard.is_dialogue_target() is False
    assert guard.is_confirmed_vendor() is False


def test_hostile_or_animal_dialogue_holder_is_not_a_talk_target() -> None:
    hostile = entity("Bandit", is_animal=False, has_dialogue=True,
                     disposition=Disposition.HOSTILE)
    beast = entity("Bonedog", is_animal=True, has_dialogue=True,
                   disposition=Disposition.NEUTRAL)
    assert hostile.is_dialogue_target() is False
    assert beast.is_dialogue_target() is False


def test_visibility_and_talk_availability_do_not_affect_confirmation() -> None:
    # The live bug: an off-screen, not-talk-available Barman is still a vendor.
    occluded = barman(visible=False, talk_task_available=False,
                      talk_task_probability=0.0, distance=366.0)
    assert occluded.is_confirmed_vendor() is True
    assert [v.name for v in confirmed_vendor_candidates([occluded])] == ["Barman"]


def test_hostile_vendor_is_excluded() -> None:
    assert barman(disposition=Disposition.HOSTILE).is_confirmed_vendor() is False


def test_unknown_disposition_is_excluded() -> None:
    assert barman(disposition=Disposition.UNKNOWN).is_confirmed_vendor() is False


def test_animal_with_vendor_flags_is_excluded() -> None:
    assert barman(is_animal=True).is_confirmed_vendor() is False


def test_missing_flags_are_never_assumed_favorable() -> None:
    # A None flag must not pass the fence.
    for field in ("is_animal", "has_vendor_list", "is_squad_leader", "has_dialogue"):
        candidate = barman(**{field: None})
        assert candidate.is_confirmed_vendor() is False, field


def test_non_leader_vendor_is_excluded() -> None:
    # A caravan follower can inherit a vendor list without being the shop leader.
    assert barman(is_squad_leader=False).is_confirmed_vendor() is False


def test_candidates_are_sorted_nearest_first() -> None:
    near = barman(distance=10.0)
    near.name = "Near Barman"
    far = barman(distance=200.0)
    far.name = "Far Barman"
    result = confirmed_vendor_candidates([far, near])
    assert [v.name for v in result] == ["Near Barman", "Far Barman"]


def test_missing_distance_sorts_last_not_crash() -> None:
    no_distance = barman(distance=None)
    no_distance.name = "Unknown Distance"
    close = barman(distance=5.0)
    close.name = "Close"
    result = confirmed_vendor_candidates([no_distance, close])
    assert [v.name for v in result] == ["Close", "Unknown Distance"]


def test_planner_payload_surfaces_deterministic_dialogue_targets() -> None:
    import json

    from kenshi_agent.models import Observation, TelemetrySnapshot

    observation = Observation(
        run_id="digest-test",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            nearby_entities=[
                barman(distance=26.0),
                entity("Mercenary Captain", is_animal=False, has_vendor_list=False,
                       is_squad_leader=True, has_dialogue=True,
                       disposition=Disposition.NEUTRAL, distance=15.0),
                entity("Bandit", is_animal=False, has_dialogue=True,
                       disposition=Disposition.HOSTILE, distance=8.0),
            ]
        ),
    )

    payload = json.loads(observation.planner_payload())
    targets = payload["dialogue_targets"]

    # Hostile bandit excluded; nearest-first (Captain 15 before Barman 26).
    assert [t["name"] for t in targets] == ["Mercenary Captain", "Barman"]
    by_name = {t["name"]: t for t in targets}
    assert by_name["Barman"]["is_vendor"] is True
    assert by_name["Mercenary Captain"]["is_vendor"] is False


def test_planner_payload_dialogue_targets_empty_without_telemetry() -> None:
    import json

    from kenshi_agent.models import Observation

    observation = Observation(run_id="no-telemetry", step_index=0, mode="mock")
    payload = json.loads(observation.planner_payload())
    assert payload["dialogue_targets"] == []
