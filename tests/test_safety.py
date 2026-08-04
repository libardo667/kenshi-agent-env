from datetime import UTC, datetime

import pytest

from kenshi_agent.action_budget import ActionBudgetError, ActionBudgetLedger
from kenshi_agent.affordances import OPERATION_BINDING_AUTHORITY
from kenshi_agent.config import MacroConfig, NormalizedPointerBoundsConfig, SafetyConfig
from kenshi_agent.core.continuity import (
    FieldbookProjectIndex,
    FieldbookProjectKind,
    FieldbookProjectStatus,
)
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    Action,
    ApproachDialogueTargetAction,
    ClickAction,
    ConsultAdvisorAction,
    ControlMode,
    CoordinateSpace,
    GameBinding,
    KeyAction,
    MoveCursorAction,
    PauseAction,
    PurchaseItemAction,
    ReadFieldbookAction,
    RecallMemoryAction,
    ScrollAction,
    SelectSquadMemberAction,
    SelectSquadMemberExactAction,
    SetSpeedAction,
    SkillAction,
    SkillArgument,
    TravelToMapDestinationAction,
    UseGameBindingAction,
    WaitAction,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    Disposition,
    GameState,
    KnownMapDestination,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NativeControlState,
    NearbyEntity,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
)
from kenshi_agent.safety import OperationPolicy, SafetyViolation, require_exact_target_id
from kenshi_agent.skills import MacroRegistry


def reserve_action(
    ledger: ActionBudgetLedger,
    action: Action,
    observation: Observation,
):
    bound = OPERATION_BINDING_AUTHORITY.bind(action, observation, affordance=None)
    return ledger.reserve(bound, observation)


def safety_config() -> SafetyConfig:
    return SafetyConfig(
        allow_action_kinds=[
            "noop",
            "stop",
            "pause",
            "set_speed",
            "wait",
            "key",
            "hotkey",
            "click",
            "move_cursor",
            "scroll",
            "skill",
        ],
        allow_skills=["open_map"],
        max_wait_seconds=3.0,
        max_actions_per_minute=100,
    )


def test_normalized_click_outside_bounds_is_blocked() -> None:
    guard = OperationPolicy(safety_config(), MacroRegistry({}))
    observation = Observation(run_id="run", step_index=0, mode="mock")
    with pytest.raises(SafetyViolation):
        guard.validate(ClickAction(x=1.1, y=0.5), observation)


def test_stale_live_click_is_blocked() -> None:
    guard = OperationPolicy(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(),
        telemetry_stale=True,
    )
    with pytest.raises(SafetyViolation):
        guard.validate(ClickAction(x=0.5, y=0.5), observation)


def test_stale_live_scroll_is_blocked() -> None:
    guard = OperationPolicy(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(),
        telemetry_stale=True,
    )
    with pytest.raises(SafetyViolation, match="telemetry is stale"):
        guard.validate(ScrollAction(x=0.5, y=0.5, notches=1), observation)


def test_live_screen_space_pointer_action_is_blocked() -> None:
    guard = OperationPolicy(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="Screen-space"):
        guard.validate(ClickAction(x=100, y=100, space=CoordinateSpace.SCREEN), observation)


def test_live_move_cursor_uses_the_same_bounds_as_clicks() -> None:
    guard = OperationPolicy(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="outside the Kenshi window"):
        guard.validate(MoveCursorAction(x=1280, y=20, space=CoordinateSpace.CLIENT), observation)


def test_live_client_pointer_requires_known_window_dimensions() -> None:
    guard = OperationPolicy(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run", step_index=0, mode="live", telemetry=TelemetrySnapshot()
    )
    with pytest.raises(SafetyViolation, match="dimensions are unknown"):
        guard.validate(ClickAction(x=20, y=20, space=CoordinateSpace.CLIENT), observation)


def test_exact_squad_selection_can_reduce_a_current_multi_selection() -> None:
    config = safety_config().model_copy(update={"allow_action_kinds": ["select_squad_member"]})
    guard = OperationPolicy(
        config,
        MacroRegistry({}),
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    action = SelectSquadMemberAction(target_id="entity-nam")
    observation = Observation(
        run_id="squad-selection",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        telemetry=TelemetrySnapshot(
            capabilities=["squad.basic", "ui.visible_controls"],
            game=GameState(loaded=True, paused=True),
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
                selected_character_id="entity-twitch",
                selected_character_ids=["entity-nam", "entity-twitch"],
                visible_controls=[
                    VisibleUIControl(
                        label="Nam",
                        role="text",
                        bounds=NormalizedPointerBounds(
                            min_x=0.32,
                            max_x=0.38,
                            min_y=0.84,
                            max_y=0.95,
                        ),
                    )
                ],
                visible_controls_complete=True,
            ),
            squad=[
                CharacterState(
                    id="entity-nam",
                    name="Nam",
                    selected=True,
                ),
                CharacterState(
                    id="entity-twitch",
                    name="Twitch",
                    selected=True,
                ),
            ],
        ),
    )

    assert guard.validate(action, observation) == action


def test_dialogue_approach_preserves_a_valid_multi_selection() -> None:
    config = safety_config().model_copy(
        update={
            "allow_action_kinds": [
                *safety_config().allow_action_kinds,
                "approach_dialogue_target",
            ]
        }
    )
    guard = OperationPolicy(
        config,
        MacroRegistry({}),
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    action = ApproachDialogueTargetAction(target_id="entity-vendor")
    observation = Observation(
        run_id="group-dialogue",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        telemetry=TelemetrySnapshot(
            identity_session_id="session-group-dialogue",
            capabilities=[
                "control.approach_vendor",
                "identity.stable_handles",
                "nearby.characters",
                "nearby.roles",
            ],
            game=GameState(loaded=True, paused=True),
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
                selected_character_id="entity-bark",
                selected_character_ids=["entity-bark", "entity-plant"],
            ),
            squad=[
                CharacterState(id="entity-bark", name="Bark", selected=True),
                CharacterState(id="entity-plant", name="Plant", selected=True),
            ],
            nearby_entities=[
                NearbyEntity(
                    id="entity-vendor",
                    name="Barman",
                    is_animal=False,
                    has_dialogue=True,
                    conscious=True,
                    disposition=Disposition.NEUTRAL,
                )
            ],
        ),
    )

    assert guard.validate(action, observation) == action


@pytest.mark.parametrize(
    "action",
    [
        SelectSquadMemberExactAction(target_id="entity-plant"),
        TravelToMapDestinationAction(destination_id="entity-hub"),
    ],
)
def test_native_party_control_accepts_an_exact_group_basis(
    action: SelectSquadMemberExactAction | TravelToMapDestinationAction,
) -> None:
    guard = OperationPolicy(
        safety_config().model_copy(update={"allow_action_kinds": [action.kind]}),
        MacroRegistry({}),
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    observation = Observation(
        run_id="native-party-control",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        telemetry=TelemetrySnapshot(
            identity_session_id="session-native-party-control",
            capabilities=[
                "control.select_squad_member",
                "control.travel_to_map_destination",
                "game.pause",
                "game.speed",
                "identity.stable_handles",
                "squad.basic",
                "squad.health",
                "world.known_map_destinations",
            ],
            game=GameState(loaded=True, paused=True, speed_multiplier=0.0),
            ui=UIState(
                active_screen="world",
                dialogue_open=False,
                modal_open=False,
                selected_character_id="entity-bark",
                selected_character_ids=["entity-bark", "entity-plant"],
            ),
            squad=[
                CharacterState(
                    id="entity-bark",
                    name="Bark",
                    selected=True,
                ),
                CharacterState(
                    id="entity-plant",
                    name="Plant",
                    selected=True,
                ),
            ],
            known_map_destinations=[
                KnownMapDestination(
                    id="entity-hub",
                    name="The Hub",
                    distance=1250.0,
                )
            ],
        ),
    )

    assert guard.validate(action, observation) == action


def test_live_pause_requires_known_current_state() -> None:
    guard = OperationPolicy(safety_config(), MacroRegistry({}))
    unknown = Observation(run_id="run", step_index=0, mode="live", telemetry=TelemetrySnapshot())
    with pytest.raises(SafetyViolation, match="pause state is unknown"):
        guard.validate(PauseAction(paused=True), unknown)

    known = unknown.model_copy(
        update={"telemetry": TelemetrySnapshot(game=GameState(paused=False))}
    )
    assert guard.validate(PauseAction(paused=True), known).paused is True

    with pytest.raises(SafetyViolation, match="Direct live unpause"):
        guard.validate(PauseAction(paused=False), known)


def test_set_speed_unpause_requires_explicit_profile_authority() -> None:
    action = SetSpeedAction(speed=3)
    paused = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            capabilities=["game.pause", "game.speed"],
            game=GameState(loaded=True, paused=True, speed_multiplier=0.0),
        ),
    )

    with pytest.raises(SafetyViolation, match="Direct live unpause"):
        OperationPolicy(safety_config(), MacroRegistry({})).validate(action, paused)

    enabled = safety_config().model_copy(update={"allow_live_unpause_actions": True})
    assert OperationPolicy(enabled, MacroRegistry({})).validate(action, paused) == action


@pytest.mark.parametrize(
    "binding",
    [
        GameBinding.PAUSE,
        GameBinding.SPEED_1,
        GameBinding.SPEED_2,
        GameBinding.SPEED_3,
    ],
)
def test_raw_time_binding_is_not_a_guarded_planner_affordance(
    binding: GameBinding,
) -> None:
    guard = OperationPolicy(
        safety_config().model_copy(
            update={"allow_action_kinds": [*safety_config().allow_action_kinds, "use_game_binding"]}
        ),
        MacroRegistry({}),
    )
    action = UseGameBindingAction(
        binding=binding,
        expected_effect="change playback",
    )
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            capabilities=["game.pause", "game.speed"],
            game=GameState(loaded=True, paused=False, speed_multiplier=1.0),
        ),
    )

    with pytest.raises(SafetyViolation, match="Raw time bindings"):
        guard.validate(action, observation)


def test_safety_pause_bypasses_only_the_rate_budget() -> None:
    config = safety_config().model_copy(update={"max_actions_per_minute": 1})
    guard = OperationPolicy(config, MacroRegistry({}))
    ledger = ActionBudgetLedger(config, guard.macros)
    observation = Observation(run_id="run", step_index=0, mode="mock")

    ledger.commit(reserve_action(ledger, PauseAction(paused=True), observation))
    with pytest.raises(ActionBudgetError, match="rate limit"):
        reserve_action(ledger, PauseAction(paused=True), observation)

    assert guard.validate_safety_pause(PauseAction(paused=True), observation).paused is True
    with pytest.raises(SafetyViolation, match="paused=true"):
        guard.validate_safety_pause(PauseAction(paused=False), observation)

    mismatched = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        telemetry=TelemetrySnapshot(game=GameState(paused=False)),
    )
    with pytest.raises(SafetyViolation, match="does not match"):
        guard.validate_safety_pause(PauseAction(paused=True), mismatched)


def test_revalidation_does_not_spend_rate_authority_twice() -> None:
    config = safety_config().model_copy(update={"max_actions_per_minute": 1})
    guard = OperationPolicy(config, MacroRegistry({}))
    ledger = ActionBudgetLedger(config, guard.macros)
    observation = Observation(run_id="run", step_index=0, mode="mock")
    action = PauseAction(paused=True)

    ledger.commit(reserve_action(ledger, action, observation))
    for _ in range(5):
        assert guard.revalidate(action, observation) == action
    with pytest.raises(ActionBudgetError, match="rate limit"):
        reserve_action(ledger, action, observation)


def test_rate_budget_conserves_committed_and_pending_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr("kenshi_agent.action_budget.time.monotonic", lambda: clock[0])
    config = safety_config().model_copy(update={"max_actions_per_minute": 2})
    guard = OperationPolicy(config, MacroRegistry({}))
    ledger = ActionBudgetLedger(config, guard.macros)
    observation = Observation(run_id="run", step_index=0, mode="mock")
    action = PauseAction(paused=True)

    first = reserve_action(ledger, action, observation)
    second = reserve_action(ledger, action, observation)
    assert [first.token, second.token] == [1, 2]
    with pytest.raises(ActionBudgetError, match="rate limit"):
        reserve_action(ledger, action, observation)

    ledger.release(first)
    with pytest.raises(RuntimeError):
        ledger.release(first)
    replacement = reserve_action(ledger, action, observation)
    assert replacement.token == 3
    ledger.commit(second)
    with pytest.raises(ActionBudgetError, match="rate limit"):
        reserve_action(ledger, action, observation)

    ledger.release(replacement)
    ledger.commit(reserve_action(ledger, action, observation))
    with pytest.raises(ActionBudgetError, match="rate limit"):
        reserve_action(ledger, action, observation)

    clock[0] = 60.0
    with pytest.raises(ActionBudgetError, match="rate limit"):
        reserve_action(ledger, action, observation)
    clock[0] = 60.001
    assert reserve_action(ledger, action, observation).token == 5


def test_cognitive_actions_do_not_consume_primitive_authority() -> None:
    config = safety_config().model_copy(
        update={
            "allow_action_kinds": [
                *safety_config().allow_action_kinds,
                "consult_advisor",
                "recall_memory",
                "read_fieldbook",
            ],
            "max_actions_per_minute": 1,
        }
    )
    guard = OperationPolicy(config, MacroRegistry({}))
    ledger = ActionBudgetLedger(config, guard.macros)
    observation = Observation(run_id="run", step_index=0, mode="mock")
    ledger.commit(reserve_action(ledger, PauseAction(paused=True), observation))

    advisor = ConsultAdvisorAction(question="What should the squad pursue next?")
    recall = RecallMemoryAction(query="gate")
    project_id = "fbp-" + "1" * 32
    fieldbook_observation = observation.model_copy(
        update={
            "fieldbook_projects": [
                FieldbookProjectIndex(
                    project_id=project_id,
                    title="Route",
                    kind=FieldbookProjectKind.ROUTE_ATLAS,
                    status=FieldbookProjectStatus.ACTIVE,
                    short_summary="Known route.",
                    entry_count=1,
                    updated_at=datetime.now(UTC),
                    selected=False,
                )
            ]
        }
    )
    read_fieldbook = ReadFieldbookAction(project_id=project_id)
    assert guard.validate(advisor, observation) == advisor
    assert guard.validate(recall, observation) == recall
    assert guard.validate(read_fieldbook, fieldbook_observation) == read_fieldbook
    assert reserve_action(ledger, advisor, observation).primitive_actions == 0
    assert reserve_action(ledger, recall, observation).primitive_actions == 0
    assert reserve_action(ledger, read_fieldbook, fieldbook_observation).primitive_actions == 0


def test_fieldbook_read_fails_closed_on_an_undelivered_project_identity() -> None:
    config = safety_config().model_copy(
        update={
            "allow_action_kinds": [
                *safety_config().allow_action_kinds,
                "read_fieldbook",
            ]
        }
    )
    guard = OperationPolicy(config, MacroRegistry({}))

    with pytest.raises(SafetyViolation, match="not present"):
        guard.validate(
            ReadFieldbookAction(project_id="fbp-" + "1" * 32),
            Observation(run_id="run", step_index=0, mode="mock"),
        )


def test_live_nonpurchase_actions_never_reserve_purchase_authority() -> None:
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(game=GameState(loaded=True, paused=False)),
    )
    contracted_config = safety_config().model_copy(
        update={
            "allow_action_kinds": [
                *safety_config().allow_action_kinds,
                "use_game_binding",
            ]
        }
    )
    contracted_macros = MacroRegistry({})
    contracted_ledger = ActionBudgetLedger(contracted_config, contracted_macros)
    contracted = reserve_action(
        contracted_ledger,
        UseGameBindingAction(binding=GameBinding.TOGGLE_MAP, expected_effect="open the map"),
        observation,
    )
    assert contracted.purchase_actions == 0

    macros = MacroRegistry({"open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}])})
    skill = reserve_action(
        ActionBudgetLedger(safety_config(), macros),
        SkillAction(name="open_map"),
        observation,
    )
    assert skill.purchase_actions == 0


def test_live_skill_must_be_configured_and_allowlisted() -> None:
    macros = MacroRegistry({"open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}])})
    guard = OperationPolicy(safety_config(), macros)
    observation = Observation(run_id="run", step_index=0, mode="live")
    action = guard.validate(SkillAction(name="open_map"), observation)
    assert action.kind == "skill"


def test_live_skill_requires_both_allowlist_and_configured_macro() -> None:
    observation = Observation(run_id="run", step_index=0, mode="live")
    macro = MacroConfig(actions=[{"kind": "key", "key": "m"}])
    with pytest.raises(SafetyViolation, match="not allowlisted"):
        OperationPolicy(
            safety_config().model_copy(update={"allow_skills": []}),
            MacroRegistry({"open_map": macro}),
        ).validate(SkillAction(name="open_map"), observation)
    with pytest.raises(SafetyViolation, match="no configured macro"):
        OperationPolicy(
            safety_config().model_copy(update={"allow_skills": ["open_map"]}),
            MacroRegistry({}),
        ).validate(SkillAction(name="open_map"), observation)


def test_interface_only_guard_rejects_native_assisted_skill() -> None:
    config = safety_config().model_copy(update={"allow_skills": ["approach_confirmed_vendor"]})
    macros = MacroRegistry(
        {
            "approach_confirmed_vendor": MacroConfig(
                requires_native_assisted=True,
                actions=[{"kind": "hotkey", "keys": ["ctrl", "shift", "f10"]}],
            )
        }
    )
    observation = Observation(run_id="run", step_index=0, mode="live")

    with pytest.raises(SafetyViolation, match="requires native_assisted"):
        OperationPolicy(config, macros, control_mode=ControlMode.INTERFACE_ONLY).validate(
            SkillAction(name="approach_confirmed_vendor"),
            observation,
        )


def test_native_assisted_guard_accepts_marked_skill_only_for_matching_observation() -> None:
    config = safety_config().model_copy(update={"allow_skills": ["approach_confirmed_vendor"]})
    macros = MacroRegistry(
        {
            "approach_confirmed_vendor": MacroConfig(
                requires_native_assisted=True,
                actions=[{"kind": "hotkey", "keys": ["ctrl", "shift", "f10"]}],
            )
        }
    )
    guard = OperationPolicy(config, macros, control_mode=ControlMode.NATIVE_ASSISTED)
    action = SkillAction(
        name="approach_confirmed_vendor",
        args={"target_id": "entity-vendor"},  # type: ignore[arg-type]
    )

    with pytest.raises(SafetyViolation, match="control mode"):
        guard.validate(action, Observation(run_id="run", step_index=0, mode="live"))

    accepted = guard.validate(
        action,
        Observation(
            run_id="run",
            step_index=0,
            mode="live",
            control_mode=ControlMode.NATIVE_ASSISTED,
            telemetry=TelemetrySnapshot(
                protocol_version="0.3.0",
                identity_session_id="session-test",
                capabilities=[
                    "game.pause",
                    "control.approach_vendor",
                    "identity.stable_handles",
                    "nearby.characters",
                    "nearby.roles",
                ],
                game=GameState(paused=True),
                ui=UIState(
                    selected_character_id="entity-selected",
                    selected_character_ids=["entity-selected", "entity-companion"],
                ),
                squad=[
                    CharacterState(
                        id="entity-selected",
                        name="Wanderer",
                        selected=True,
                    ),
                    CharacterState(
                        id="entity-companion",
                        name="Companion",
                        selected=True,
                    ),
                ],
                nearby_entities=[
                    NearbyEntity(
                        id="entity-vendor",
                        name="Barman",
                        is_animal=False,
                        has_vendor_list=True,
                        is_squad_leader=True,
                        has_dialogue=True,
                        conscious=True,
                        disposition=Disposition.NEUTRAL,
                    )
                ],
            ),
        ),
    )
    assert accepted == action


def native_vendor_observation(*, with_active_command: bool = False) -> Observation:
    command_id = "cmd-" + ("a" * 32)
    acknowledgement = NativeCommandAcknowledgement(
        command_id=command_id,
        command="approach_confirmed_vendor",
        status=NativeCommandStatus.ACCEPTED,
        reason="issued",
        target_id="entity-vendor",
        selected_character_ids=["entity-selected"],
        based_on_telemetry_sequence=1,
        acknowledged_at_telemetry_sequence=2,
        accepted_at_telemetry_sequence=2,
    )
    return Observation(
        run_id="native-vendor",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        telemetry=TelemetrySnapshot(
            sequence=2,
            identity_session_id="session-test",
            capabilities=[
                "control.approach_vendor",
                "identity.stable_handles",
                "nearby.characters",
                "nearby.roles",
            ],
            ui=UIState(
                selected_character_id="entity-selected",
                selected_character_ids=["entity-selected"],
            ),
            native_control=NativeControlState(
                available=True,
                active_command_id=command_id if with_active_command else None,
                acknowledgements=[acknowledgement] if with_active_command else [],
            ),
            squad=[
                CharacterState(
                    id="entity-selected",
                    name="Wanderer",
                    selected=True,
                )
            ],
            nearby_entities=[
                NearbyEntity(
                    id="entity-vendor",
                    name="Barman",
                    is_animal=False,
                    has_vendor_list=True,
                    is_squad_leader=True,
                    has_dialogue=True,
                    conscious=True,
                    disposition=Disposition.NEUTRAL,
                )
            ],
        ),
    )


def native_vendor_guard(skill_name: str) -> OperationPolicy:
    config = safety_config().model_copy(update={"allow_skills": [skill_name]})
    macros = MacroRegistry(
        {
            skill_name: MacroConfig(
                requires_native_assisted=True,
                actions=[{"kind": "hotkey", "keys": ["ctrl", "shift", "f10"]}],
            )
        }
    )
    return OperationPolicy(config, macros, control_mode=ControlMode.NATIVE_ASSISTED)


@pytest.mark.parametrize(
    "failed_fact",
    [
        "stale",
        "missing_telemetry",
        "missing_capability",
        "no_selection",
        "no_primary_selection",
        "missing_target",
        "animal",
        "no_vendor_list",
        "not_leader",
        "no_dialogue",
        "unconscious",
        "hostile",
    ],
)
def test_native_vendor_target_requires_every_independent_fact(failed_fact: str) -> None:
    observation = native_vendor_observation()
    telemetry = observation.telemetry
    assert telemetry is not None
    if failed_fact == "stale":
        observation = observation.model_copy(update={"telemetry_stale": True})
    elif failed_fact == "missing_telemetry":
        observation = observation.model_copy(update={"telemetry": None})
    elif failed_fact == "missing_capability":
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "capabilities": [
                            value for value in telemetry.capabilities if value != "nearby.roles"
                        ]
                    }
                )
            }
        )
    elif failed_fact == "no_selection":
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "ui": telemetry.ui.model_copy(
                            update={
                                "selected_character_id": None,
                                "selected_character_ids": [],
                            }
                        ),
                        "squad": [telemetry.squad[0].model_copy(update={"selected": False})],
                    }
                )
            }
        )
    elif failed_fact == "no_primary_selection":
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"ui": telemetry.ui.model_copy(update={"selected_character_id": None})}
                )
            }
        )
    elif failed_fact == "missing_target":
        observation = observation.model_copy(
            update={"telemetry": telemetry.model_copy(update={"nearby_entities": []})}
        )
    else:
        field_updates: dict[str, object] = {
            "animal": {"is_animal": True},
            "no_vendor_list": {"has_vendor_list": False},
            "not_leader": {"is_squad_leader": False},
            "no_dialogue": {"has_dialogue": False},
            "unconscious": {"conscious": False},
            "hostile": {"disposition": Disposition.HOSTILE},
        }[failed_fact]
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "nearby_entities": [
                            telemetry.nearby_entities[0].model_copy(update=field_updates)
                        ]
                    }
                )
            }
        )

    action = SkillAction(
        name="approach_confirmed_vendor",
        args={"target_id": "entity-vendor"},  # type: ignore[arg-type]
    )
    with pytest.raises(SafetyViolation):
        native_vendor_guard(action.name).validate(action, observation)


@pytest.mark.parametrize("target_id", [None, ""])
def test_native_vendor_target_id_must_be_a_nonempty_string(
    target_id: str | None,
) -> None:
    args = {} if target_id is None else {"target_id": target_id}
    action = SkillAction(
        name="approach_confirmed_vendor",
        args=args,  # type: ignore[arg-type]
    )
    with pytest.raises(SafetyViolation):
        native_vendor_guard(action.name).validate(action, native_vendor_observation())


@pytest.mark.parametrize("value", [None, "", 7, False])
def test_exact_target_id_boundary_rejects_every_non_reference(value: object) -> None:
    with pytest.raises(SafetyViolation):
        require_exact_target_id(value)


def test_exact_target_id_boundary_preserves_the_reference() -> None:
    assert require_exact_target_id("entity-vendor") == "entity-vendor"


def test_native_vendor_continuation_binds_exact_active_acknowledgement() -> None:
    name = "continue_confirmed_vendor_approach"
    action = SkillAction(
        name=name,
        args={"target_id": "entity-vendor"},  # type: ignore[arg-type]
    )
    accepted = native_vendor_observation(with_active_command=True)
    assert native_vendor_guard(name).validate(action, accepted) == action

    telemetry = accepted.telemetry
    assert telemetry is not None
    acknowledgement = telemetry.native_control.acknowledgements[0]
    variants = [
        accepted.model_copy(update={"telemetry_stale": True}),
        accepted.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "native_control": telemetry.native_control.model_copy(
                            update={"active_command_id": None}
                        )
                    }
                )
            }
        ),
    ]
    for acknowledgement_update in [
        {"status": NativeCommandStatus.COMPLETED},
        {"target_id": "entity-other"},
        {"selected_character_ids": ["entity-other"]},
    ]:
        variants.append(
            accepted.model_copy(
                update={
                    "telemetry": telemetry.model_copy(
                        update={
                            "native_control": telemetry.native_control.model_copy(
                                update={
                                    "acknowledgements": [
                                        acknowledgement.model_copy(update=acknowledgement_update)
                                    ]
                                }
                            )
                        }
                    )
                }
            )
        )

    for observation in variants:
        with pytest.raises(SafetyViolation):
            native_vendor_guard(name).validate(action, observation)


def test_native_vendor_target_accepts_both_nonhostile_dispositions() -> None:
    action = SkillAction(
        name="approach_confirmed_vendor",
        args={"target_id": "entity-vendor"},  # type: ignore[arg-type]
    )
    neutral = native_vendor_observation()
    telemetry = neutral.telemetry
    assert telemetry is not None
    friendly = neutral.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "nearby_entities": [
                        telemetry.nearby_entities[0].model_copy(
                            update={"disposition": Disposition.FRIENDLY}
                        )
                    ]
                }
            )
        }
    )
    assert native_vendor_guard(action.name).validate(action, neutral) == action
    assert native_vendor_guard(action.name).validate(action, friendly) == action


def test_allowlisted_skill_can_expand_to_a_blocked_top_level_primitive() -> None:
    config = safety_config().model_copy(
        update={"allow_action_kinds": ["noop", "stop", "wait", "skill"]}
    )
    macros = MacroRegistry({"open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}])})
    guard = OperationPolicy(config, macros)
    observation = Observation(run_id="run", step_index=0, mode="live")

    assert guard.validate(SkillAction(name="open_map"), observation).kind == "skill"
    with pytest.raises(SafetyViolation, match="Action kind 'key'"):
        guard.validate(KeyAction(key="m"), observation)


def test_skill_primitive_boundary_includes_every_supported_pointer_kind() -> None:
    config = safety_config().model_copy(
        update={
            "allow_action_kinds": ["skill"],
            "allow_skills": ["pointer_pair"],
            "max_primitive_actions_per_step": 2,
        }
    )
    macros = MacroRegistry(
        {
            "pointer_pair": MacroConfig(
                actions=[
                    {
                        "kind": "move_cursor",
                        "x": 0.25,
                        "y": 0.25,
                        "space": "normalized",
                    },
                    {
                        "kind": "scroll",
                        "x": 0.75,
                        "y": 0.75,
                        "space": "normalized",
                        "notches": 1,
                    },
                ]
            )
        }
    )
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(),
    )

    action = SkillAction(name="pointer_pair")
    assert OperationPolicy(config, macros).validate(action, observation) == action
    with pytest.raises(SafetyViolation, match="maximum is 1"):
        OperationPolicy(
            config.model_copy(update={"max_primitive_actions_per_step": 1}),
            macros,
        ).validate(action, observation)


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (0.0, 0.0),
        (0.0, 1.0),
        (1.0, 0.0),
        (1.0, 1.0),
    ],
)
def test_normalized_pointer_boundary_is_closed(x: float, y: float) -> None:
    action = MoveCursorAction(x=x, y=y, space=CoordinateSpace.NORMALIZED)
    observation = Observation(run_id="run", step_index=0, mode="mock")
    assert (
        OperationPolicy(safety_config(), MacroRegistry({})).validate(action, observation) == action
    )


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (-0.001, 0.5),
        (1.001, 0.5),
        (0.5, -0.001),
        (0.5, 1.001),
    ],
)
def test_each_normalized_pointer_axis_fails_closed(x: float, y: float) -> None:
    with pytest.raises(SafetyViolation):
        OperationPolicy(safety_config(), MacroRegistry({})).validate(
            MoveCursorAction(x=x, y=y, space=CoordinateSpace.NORMALIZED),
            Observation(run_id="run", step_index=0, mode="mock"),
        )


@pytest.mark.parametrize(
    ("x", "y"),
    [(-0.001, 5.0), (5.0, -0.001)],
)
def test_each_client_pointer_axis_rejects_negative_values(x: float, y: float) -> None:
    with pytest.raises(SafetyViolation):
        OperationPolicy(safety_config(), MacroRegistry({})).validate(
            MoveCursorAction(x=x, y=y, space=CoordinateSpace.CLIENT),
            Observation(run_id="run", step_index=0, mode="mock"),
        )


def test_client_pointer_boundary_uses_both_current_dimensions() -> None:
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    accepted = MoveCursorAction(x=1279, y=719, space=CoordinateSpace.CLIENT)
    assert (
        OperationPolicy(safety_config(), MacroRegistry({})).validate(accepted, observation)
        == accepted
    )
    for x, y in [(1280, 719), (1279, 720)]:
        with pytest.raises(SafetyViolation, match="outside the Kenshi window"):
            OperationPolicy(safety_config(), MacroRegistry({})).validate(
                MoveCursorAction(x=x, y=y, space=CoordinateSpace.CLIENT),
                observation,
            )


@pytest.mark.parametrize(
    ("width", "height"),
    [(None, 720), (1280, None)],
)
def test_live_client_pointer_requires_each_dimension(
    width: int | None,
    height: int | None,
) -> None:
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=width, client_height=height)),
    )
    with pytest.raises(SafetyViolation, match="dimensions are unknown"):
        OperationPolicy(safety_config(), MacroRegistry({})).validate(
            MoveCursorAction(x=0, y=0, space=CoordinateSpace.CLIENT),
            observation,
        )


def test_live_skill_primitives_receive_pointer_validation() -> None:
    macros = MacroRegistry(
        {
            "open_map": MacroConfig(
                actions=[{"kind": "click", "x": 100, "y": 100, "space": "screen"}]
            )
        }
    )
    guard = OperationPolicy(safety_config(), macros)
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="Screen-space"):
        guard.validate(SkillAction(name="open_map"), observation)


def test_live_movement_skill_is_confined_to_its_calibrated_envelope() -> None:
    config = safety_config().model_copy(update={"allow_skills": ["move_on_map"]})
    macros = MacroRegistry(
        {
            "move_on_map": MacroConfig(
                normalized_pointer_bounds=NormalizedPointerBoundsConfig(
                    min_x=0.30,
                    max_x=0.68,
                    min_y=0.16,
                    max_y=0.69,
                ),
                actions=[
                    {
                        "kind": "click",
                        "x": "{{x}}",
                        "y": "{{y}}",
                        "space": "normalized",
                        "button": "right",
                    }
                ],
            )
        }
    )
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(),
        telemetry_stale=False,
    )
    guard = OperationPolicy(config, macros)

    accepted = guard.validate(
        SkillAction(
            name="move_on_map",
            args=[SkillArgument(name="x", value=0.5), SkillArgument(name="y", value=0.4)],
        ),
        observation,
    )
    assert isinstance(accepted, SkillAction)
    assert accepted.name == "move_on_map"

    with pytest.raises(SafetyViolation, match="calibrated safety envelope"):
        guard.validate(
            SkillAction(
                name="move_on_map",
                args=[
                    SkillArgument(name="x", value=0.2),
                    SkillArgument(name="y", value=0.4),
                ],
            ),
            observation,
        )


def test_live_movement_skill_rejects_missing_coordinates_as_safety_violation() -> None:
    config = safety_config().model_copy(update={"allow_skills": ["move_on_map"]})
    macros = MacroRegistry(
        {
            "move_on_map": MacroConfig(
                actions=[
                    {
                        "kind": "click",
                        "x": "{{x}}",
                        "y": "{{y}}",
                        "space": "normalized",
                        "button": "right",
                    }
                ]
            )
        }
    )
    observation = Observation(run_id="run", step_index=0, mode="live")

    with pytest.raises(SafetyViolation, match="Missing skill argument: y"):
        OperationPolicy(config, macros).validate(
            SkillAction(name="move_on_map", args=[SkillArgument(name="x", value=0.5)]),
            observation,
        )


def test_live_movement_pulse_requires_confirmed_pause() -> None:
    config = safety_config().model_copy(update={"allow_skills": ["move_on_map"]})
    macros = MacroRegistry(
        {
            "move_on_map": MacroConfig(
                movement_pulse_seconds=1.0,
                actions=[
                    {
                        "kind": "click",
                        "x": "{{x}}",
                        "y": "{{y}}",
                        "space": "normalized",
                        "button": "right",
                    }
                ],
            )
        }
    )
    action = SkillAction.model_validate({"name": "move_on_map", "args": {"x": 0.5, "y": 0.4}})
    unpaused = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(game=GameState(paused=False)),
    )

    with pytest.raises(SafetyViolation, match="requires confirmed paused"):
        OperationPolicy(config, macros).validate(action, unpaused)


def test_live_movement_pulse_rejects_duration_outside_bounds() -> None:
    config = safety_config().model_copy(update={"allow_skills": ["move_on_map"]})
    macros = MacroRegistry(
        {
            "move_on_map": MacroConfig(
                movement_pulse_seconds=2.0,
                movement_pulse_min_seconds=1.0,
                movement_pulse_max_seconds=4.0,
                actions=[],
            )
        }
    )
    action = SkillAction.model_validate({"name": "move_on_map", "args": {"duration_seconds": 8.0}})
    paused = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(game=GameState(paused=True)),
    )

    with pytest.raises(SafetyViolation, match="outside the calibrated range"):
        OperationPolicy(config, macros).validate(action, paused)


def generic_purchase_observation() -> Observation:
    bounds = NormalizedPointerBounds(
        min_x=0.30,
        max_x=0.34,
        min_y=0.20,
        max_y=0.24,
    )
    return Observation(
        run_id="generic-purchase",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            identity_session_id="session-generic-purchase",
            capabilities=[
                "ui.visible_controls",
                "ui.tooltip",
                "ui.inventory",
                "game.money",
                "game.pause",
                "identity.stable_handles",
                "nearby.characters",
                "nearby.shop_owners",
                "squad.basic",
                "squad.inventory",
            ],
            game=GameState(loaded=True, paused=True, money=1000),
            ui=UIState(
                active_screen="trade",
                selected_character_id="player:1",
                selected_character_ids=["player:1"],
                tooltip_visible=True,
                tooltip_text="Dried Meat\n[Food]\nValue: c.38",
                tooltip_source_bounds=bounds,
                visible_controls=[
                    VisibleUIControl(
                        label="HEP",
                        role="text",
                        window="HEP",
                        bounds=NormalizedPointerBounds(
                            min_x=0.10,
                            max_x=0.14,
                            min_y=0.20,
                            max_y=0.24,
                        ),
                    ),
                    VisibleUIControl(
                        label="item_3",
                        role="item",
                        window="BARMAN",
                        item_name="Dried Meat",
                        item_base_value=38,
                        bounds=bounds,
                    ),
                ],
            ),
            squad=[
                CharacterState(
                    id="player:1",
                    name="Hep",
                    selected=True,
                    inventory_complete=True,
                )
            ],
            nearby_entities=[
                NearbyEntity(
                    id="seller:1",
                    name="Barman",
                    shop_inventory_owner=True,
                    disposition=Disposition.NEUTRAL,
                )
            ],
        ),
    )


def generic_purchase_action() -> PurchaseItemAction:
    return PurchaseItemAction(
        cell_label="item_3",
        item_name="Dried Meat",
        expected_price=38,
        window="BARMAN",
        seller_id="seller:1",
    )


def generic_purchase_config() -> SafetyConfig:
    return safety_config().model_copy(
        update={
            "allow_action_kinds": [
                *safety_config().allow_action_kinds,
                "purchase_item",
            ],
            "max_purchase_price": 38,
            "min_money_after_purchase": 962,
            "max_purchases_per_run": 2,
            "required_purchase_tooltip_markers": ["[Food]"],
        }
    )


def test_generic_purchase_budget_conserves_pending_and_committed_authority() -> None:
    guard = OperationPolicy(generic_purchase_config(), MacroRegistry({}))
    ledger = ActionBudgetLedger(guard.config, guard.macros)
    action = generic_purchase_action()
    observation = generic_purchase_observation()

    first = reserve_action(ledger, action, observation)
    second = reserve_action(ledger, action, observation)
    with pytest.raises(ActionBudgetError, match="purchase limit"):
        reserve_action(ledger, action, observation)

    for _ in range(3):
        assert guard.revalidate(action, observation) == action
    ledger.commit(first)
    ledger.release(second)
    replacement = reserve_action(ledger, action, observation)
    ledger.commit(replacement)
    with pytest.raises(ActionBudgetError, match="purchase limit"):
        reserve_action(ledger, action, observation)

    one_at_a_time = ActionBudgetLedger(generic_purchase_config(), MacroRegistry({}))
    one_at_a_time.commit(reserve_action(one_at_a_time, action, observation))
    second_after_commit = reserve_action(one_at_a_time, action, observation)
    assert second_after_commit.purchase_actions == 1


def test_bounded_purchase_reserves_every_unit_and_its_total_spend() -> None:
    action = generic_purchase_action().model_copy(update={"quantity": 2})
    observation = generic_purchase_observation()
    config = generic_purchase_config().model_copy(
        update={
            "min_money_after_purchase": 924,
            "max_purchases_per_run": 2,
        }
    )
    guard = OperationPolicy(config, MacroRegistry({}))
    ledger = ActionBudgetLedger(config, guard.macros)

    reservation = reserve_action(ledger, action, observation)
    assert reservation.purchase_actions == 2
    assert reservation.primitive_actions == 4
    with pytest.raises(ActionBudgetError, match="purchase limit"):
        reserve_action(ledger, generic_purchase_action(), observation)

    too_little_reserve = config.model_copy(update={"min_money_after_purchase": 925})
    with pytest.raises(SafetyViolation, match="would leave 924 cats"):
        OperationPolicy(too_little_reserve, MacroRegistry({})).validate(action, observation)


def test_generic_purchase_limits_are_inclusive_and_independent() -> None:
    action = generic_purchase_action()
    observation = generic_purchase_observation()
    assert (
        OperationPolicy(generic_purchase_config(), MacroRegistry({})).validate(
            action,
            observation,
        )
        == action
    )

    for config_update, observation_update in [
        ({"max_purchase_price": 37}, {}),
        ({"min_money_after_purchase": 963}, {}),
        ({"required_purchase_tooltip_markers": ["[Medical]"]}, {}),
        (
            {},
            {
                "telemetry": observation.telemetry.model_copy(
                    update={
                        "ui": observation.telemetry.ui.model_copy(
                            update={"active_screen": "inventory"}
                        )
                    }
                )
            },
        ),
        (
            {},
            {
                "telemetry": observation.telemetry.model_copy(
                    update={
                        "ui": observation.telemetry.ui.model_copy(
                            update={"selected_character_ids": []}
                        )
                    }
                )
            },
        ),
    ]:
        with pytest.raises(SafetyViolation):
            OperationPolicy(
                generic_purchase_config().model_copy(update=config_update),
                MacroRegistry({}),
            ).validate(action, observation.model_copy(update=observation_update))


def test_generic_purchase_budget_uses_the_bound_window_owner_in_a_group() -> None:
    observation = generic_purchase_observation()
    assert observation.telemetry is not None
    plant = CharacterState(
        id="player:2",
        name="Plant",
        selected=True,
        inventory_complete=True,
    )
    grouped = observation.model_copy(
        update={
            "telemetry": observation.telemetry.model_copy(
                update={
                    "squad": [plant, *observation.telemetry.squad],
                    "ui": observation.telemetry.ui.model_copy(
                        update={
                            "selected_character_id": plant.id,
                            "selected_character_ids": ["player:1", plant.id],
                        }
                    ),
                }
            )
        },
        deep=True,
    )

    assert (
        OperationPolicy(generic_purchase_config(), MacroRegistry({})).validate(
            generic_purchase_action(),
            grouped,
        )
        == generic_purchase_action()
    )


def test_generic_purchase_marker_requires_real_tooltip_text() -> None:
    observation = generic_purchase_observation()
    telemetry = observation.telemetry
    assert telemetry is not None
    no_tooltip_text = observation.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={"ui": telemetry.ui.model_copy(update={"tooltip_text": None})}
            )
        }
    )
    config = generic_purchase_config().model_copy(
        update={"required_purchase_tooltip_markers": ["XXXX"]}
    )
    with pytest.raises(SafetyViolation):
        OperationPolicy(config, MacroRegistry({})).validate(
            generic_purchase_action(),
            no_tooltip_text,
        )


@pytest.mark.parametrize(
    "failed_fact",
    ["stale", "missing_telemetry", "missing_capability"],
)
def test_contracted_purchase_requires_fresh_capable_live_state(
    failed_fact: str,
) -> None:
    observation = generic_purchase_observation()
    telemetry = observation.telemetry
    assert telemetry is not None
    if failed_fact == "stale":
        observation = observation.model_copy(update={"telemetry_stale": True})
    elif failed_fact == "missing_telemetry":
        observation = observation.model_copy(update={"telemetry": None})
    else:
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "capabilities": [
                            capability
                            for capability in telemetry.capabilities
                            if capability != "ui.tooltip"
                        ]
                    }
                )
            }
        )

    with pytest.raises(SafetyViolation):
        OperationPolicy(generic_purchase_config(), MacroRegistry({})).validate(
            generic_purchase_action(),
            observation,
        )


def test_mock_purchase_contract_spends_neither_live_purchase_authority_nor_evidence() -> None:
    config = generic_purchase_config().model_copy(update={"max_purchases_per_run": 0})
    guard = OperationPolicy(config, MacroRegistry({}))
    observation = generic_purchase_observation().model_copy(
        update={"run_id": "mock-purchase", "mode": "mock"}
    )
    action = generic_purchase_action()

    assert guard.validate(action, observation) == action
    assert guard.revalidate(action, observation) == action
    ledger = ActionBudgetLedger(config, guard.macros)
    assert reserve_action(ledger, action, observation).purchase_actions == 0


def purchase_observation(*, include_tooltip: bool = True) -> Observation:
    ui: dict[str, object] = {
        "active_screen": "trade",
        "selected_character_id": "player:1",
        "selected_character_ids": ["player:1"],
        "visible_controls_complete": True,
        "visible_controls": [
            {
                "label": "item_3",
                "role": "item",
                "window": "BARMAN",
                "bounds": {
                    "min_x": 0.30,
                    "max_x": 0.34,
                    "min_y": 0.34,
                    "max_y": 0.38,
                },
            }
        ],
    }
    if include_tooltip:
        ui.update(
            {
                "tooltip_visible": True,
                "tooltip_text": "Dried Meat\n[Food]\nValue: c.649",
                "tooltip_source_bounds": {
                    "min_x": 0.30,
                    "max_x": 0.34,
                    "min_y": 0.34,
                    "max_y": 0.38,
                },
            }
        )
    return Observation.model_validate(
        {
            "run_id": "purchase",
            "step_index": 0,
            "mode": "live",
            "telemetry_stale": False,
            "telemetry": {
                "identity_session_id": "session-purchase",
                "capabilities": [
                    "game.money",
                    "game.pause",
                    "identity.stable_handles",
                    "nearby.characters",
                    "nearby.shop_owners",
                    "squad.basic",
                    "squad.hunger",
                    "ui.inventory",
                    "ui.tooltip",
                    "ui.visible_controls",
                ],
                "game": {"paused": True, "money": 1000},
                "ui": ui,
                "squad": [{"id": "player:1", "name": "Green", "selected": True}],
                "active_shop_trader_count": 1,
                "nearby_entities": [
                    {
                        "id": "nearby:0",
                        "name": "Barman",
                        "shop_inventory_owner": True,
                        "disposition": "neutral",
                    }
                ],
            },
        }
    )


def legacy_purchase_config() -> SafetyConfig:
    return safety_config().model_copy(
        update={
            "allow_skills": ["buy_inspected_shop_item"],
            "max_purchase_price": 750,
            "min_money_after_purchase": 250,
        }
    )


def legacy_purchase_guard() -> OperationPolicy:
    return OperationPolicy(
        legacy_purchase_config(),
        MacroRegistry({"buy_inspected_shop_item": MacroConfig(actions=[])}),
    )


def legacy_purchase_action(**overrides: object) -> SkillAction:
    args: dict[str, object] = {
        "target_id": "nearby:0",
        "item_name": "Dried Meat",
        "x": 0.316,
        "y": 0.357,
        "expected_price": 649,
    }
    args.update(overrides)
    return SkillAction.model_validate({"name": "buy_inspected_shop_item", "args": args})


@pytest.mark.parametrize(
    "failed_fact",
    [
        "stale",
        "missing_telemetry",
        "missing_capability",
        "no_primary_selection",
        "wrong_window_owner",
        "missing_target",
        "not_shop_owner",
        "hostile",
    ],
)
def test_legacy_purchase_requires_every_independent_authority_fact(
    failed_fact: str,
) -> None:
    observation = purchase_observation()
    telemetry = observation.telemetry
    assert telemetry is not None
    if failed_fact == "stale":
        observation = observation.model_copy(update={"telemetry_stale": True})
    elif failed_fact == "missing_telemetry":
        observation = observation.model_copy(update={"telemetry": None})
    elif failed_fact == "missing_capability":
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "capabilities": [
                            capability
                            for capability in telemetry.capabilities
                            if capability != "ui.tooltip"
                        ]
                    }
                )
            }
        )
    elif failed_fact == "no_primary_selection":
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"ui": telemetry.ui.model_copy(update={"selected_character_id": None})}
                )
            }
        )
    elif failed_fact == "wrong_window_owner":
        assert telemetry.ui.visible_controls is not None
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "ui": telemetry.ui.model_copy(
                            update={
                                "visible_controls": [
                                    telemetry.ui.visible_controls[0].model_copy(
                                        update={"window": "OTHER SHOP"}
                                    )
                                ]
                            }
                        )
                    }
                )
            }
        )
    elif failed_fact == "missing_target":
        observation = observation.model_copy(
            update={"telemetry": telemetry.model_copy(update={"nearby_entities": []})}
        )
    else:
        target_update: dict[str, object] = {
            "not_shop_owner": {"shop_inventory_owner": False},
            "hostile": {"disposition": Disposition.HOSTILE},
        }[failed_fact]
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "nearby_entities": [
                            telemetry.nearby_entities[0].model_copy(update=target_update)
                        ]
                    }
                )
            }
        )

    with pytest.raises(SafetyViolation):
        legacy_purchase_guard().validate(legacy_purchase_action(), observation)


def test_loaded_shop_trader_count_never_selects_the_current_legacy_seller() -> None:
    action = legacy_purchase_action()

    for loaded_shop_traders in range(257):
        observation = purchase_observation()
        telemetry = observation.telemetry
        assert telemetry is not None
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"active_shop_trader_count": loaded_shop_traders}
                )
            }
        )

        assert legacy_purchase_guard().validate(action, observation) == action


@pytest.mark.parametrize("target_id", [None, "", 7])
def test_legacy_purchase_target_id_must_be_a_nonempty_string(
    target_id: object,
) -> None:
    with pytest.raises(SafetyViolation, match="exact target_id"):
        legacy_purchase_guard().validate(
            legacy_purchase_action(target_id=target_id),
            purchase_observation(),
        )


def test_legacy_purchase_accepts_inclusive_price_and_balance_boundaries() -> None:
    for expected_price in [1, 750]:
        observation = purchase_observation()
        telemetry = observation.telemetry
        assert telemetry is not None
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "ui": telemetry.ui.model_copy(
                            update={
                                "tooltip_text": (f"Dried Meat\n[Food]\nValue: c.{expected_price}")
                            }
                        )
                    }
                )
            }
        )
        action = legacy_purchase_action(expected_price=expected_price)
        assert legacy_purchase_guard().validate(action, observation) == action

    zero = purchase_observation()
    zero_telemetry = zero.telemetry
    assert zero_telemetry is not None
    zero = zero.model_copy(
        update={
            "telemetry": zero_telemetry.model_copy(
                update={
                    "ui": zero_telemetry.ui.model_copy(
                        update={"tooltip_text": "Dried Meat\n[Food]\nValue: c.0"}
                    )
                }
            )
        }
    )
    with pytest.raises(SafetyViolation, match="positive integer"):
        legacy_purchase_guard().validate(
            legacy_purchase_action(expected_price=0),
            zero,
        )


def test_legacy_purchase_accepts_each_nonhostile_seller_disposition() -> None:
    neutral = purchase_observation()
    telemetry = neutral.telemetry
    assert telemetry is not None
    friendly = neutral.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "nearby_entities": [
                        telemetry.nearby_entities[0].model_copy(
                            update={"disposition": Disposition.FRIENDLY}
                        )
                    ]
                }
            )
        }
    )
    action = legacy_purchase_action()
    assert legacy_purchase_guard().validate(action, neutral) == action
    assert legacy_purchase_guard().validate(action, friendly) == action


@pytest.mark.parametrize(
    "ui_update",
    [
        {"tooltip_visible": False},
        {"tooltip_text": None},
        {"tooltip_source_bounds": None},
        {"tooltip_text": "Ration Pack\n[Food]\nValue: c.649"},
        {"tooltip_text": "Dried Meat\nValue: c.649"},
        {"tooltip_text": "Dried Meat\n[Food]\nValue: c.650"},
    ],
)
def test_legacy_purchase_requires_each_exact_tooltip_fact(
    ui_update: dict[str, object],
) -> None:
    observation = purchase_observation()
    telemetry = observation.telemetry
    assert telemetry is not None
    observation = observation.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={"ui": telemetry.ui.model_copy(update=ui_update)}
            )
        }
    )
    with pytest.raises(SafetyViolation):
        legacy_purchase_guard().validate(legacy_purchase_action(), observation)


@pytest.mark.parametrize("item_name", [None, "", "Ration Pack"])
def test_legacy_purchase_requires_the_exact_nonempty_item_name(
    item_name: object,
) -> None:
    with pytest.raises(SafetyViolation):
        legacy_purchase_guard().validate(
            legacy_purchase_action(item_name=item_name),
            purchase_observation(),
        )


@pytest.mark.parametrize(
    ("action_update", "bounds"),
    [
        ({"x": "bad"}, None),
        ({"y": "bad"}, None),
        ({"x": 0.2}, None),
        ({"y": 0.2}, None),
        (
            {"x": True, "y": 0.5},
            NormalizedPointerBounds(min_x=0.9, max_x=1.0, min_y=0.4, max_y=0.6),
        ),
        (
            {"x": 0.5, "y": True},
            NormalizedPointerBounds(min_x=0.4, max_x=0.6, min_y=0.9, max_y=1.0),
        ),
    ],
)
def test_legacy_purchase_coordinates_require_independent_numeric_containment(
    action_update: dict[str, object],
    bounds: NormalizedPointerBounds | None,
) -> None:
    observation = purchase_observation()
    telemetry = observation.telemetry
    assert telemetry is not None
    if bounds is not None:
        observation = observation.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"ui": telemetry.ui.model_copy(update={"tooltip_source_bounds": bounds})}
                )
            }
        )
    with pytest.raises(SafetyViolation):
        legacy_purchase_guard().validate(
            legacy_purchase_action(**action_update),
            observation,
        )


def test_purchase_requires_verified_owner_budget_and_one_per_run() -> None:
    config = safety_config().model_copy(
        update={
            "allow_skills": ["buy_inspected_shop_item"],
            "max_purchase_price": 750,
            "min_money_after_purchase": 250,
            "max_purchases_per_run": 1,
        }
    )
    macros = MacroRegistry(
        {
            "buy_inspected_shop_item": MacroConfig(
                actions=[
                    {
                        "kind": "click",
                        "x": "{{x}}",
                        "y": "{{y}}",
                        "space": "normalized",
                        "button": "right",
                    }
                ]
            )
        }
    )
    action = SkillAction.model_validate(
        {
            "name": "buy_inspected_shop_item",
            "args": {
                "target_id": "nearby:0",
                "item_name": "Dried Meat",
                "x": 0.316,
                "y": 0.357,
                "expected_price": 649,
            },
        }
    )
    guard = OperationPolicy(config, macros)
    ledger = ActionBudgetLedger(config, macros)
    observation = purchase_observation()

    assert guard.validate(action, observation) == action
    ledger.commit(reserve_action(ledger, action, observation))
    with pytest.raises(ActionBudgetError, match="purchase limit"):
        reserve_action(ledger, action, observation)


def test_purchase_revalidation_does_not_spend_purchase_authority_twice() -> None:
    config = safety_config().model_copy(
        update={
            "allow_skills": ["buy_inspected_shop_item"],
            "max_purchase_price": 750,
            "min_money_after_purchase": 250,
            "max_purchases_per_run": 1,
        }
    )
    macros = MacroRegistry(
        {
            "buy_inspected_shop_item": MacroConfig(
                actions=[
                    {
                        "kind": "click",
                        "x": "{{x}}",
                        "y": "{{y}}",
                        "space": "normalized",
                        "button": "right",
                    }
                ]
            )
        }
    )
    action = SkillAction.model_validate(
        {
            "name": "buy_inspected_shop_item",
            "args": {
                "target_id": "nearby:0",
                "item_name": "Dried Meat",
                "x": 0.316,
                "y": 0.357,
                "expected_price": 649,
            },
        }
    )
    guard = OperationPolicy(config, macros)
    ledger = ActionBudgetLedger(config, macros)
    observation = purchase_observation()

    ledger.commit(reserve_action(ledger, action, observation))
    for _ in range(5):
        assert guard.revalidate(action, observation) == action
    with pytest.raises(ActionBudgetError, match="purchase limit"):
        reserve_action(ledger, action, observation)


def test_released_purchase_reservation_does_not_spend_per_run_authority() -> None:
    config = safety_config().model_copy(
        update={
            "allow_skills": ["buy_inspected_shop_item"],
            "max_purchase_price": 750,
            "min_money_after_purchase": 250,
            "max_purchases_per_run": 1,
        }
    )
    macros = MacroRegistry(
        {
            "buy_inspected_shop_item": MacroConfig(
                actions=[
                    {
                        "kind": "click",
                        "x": "{{x}}",
                        "y": "{{y}}",
                        "space": "normalized",
                        "button": "right",
                    }
                ]
            )
        }
    )
    action = SkillAction.model_validate(
        {
            "name": "buy_inspected_shop_item",
            "args": {
                "target_id": "nearby:0",
                "item_name": "Dried Meat",
                "x": 0.316,
                "y": 0.357,
                "expected_price": 649,
            },
        }
    )
    guard = OperationPolicy(config, macros)
    ledger = ActionBudgetLedger(config, macros)
    observation = purchase_observation()

    reservation = reserve_action(ledger, action, observation)
    with pytest.raises(ActionBudgetError, match="purchase limit"):
        reserve_action(ledger, action, observation)
    ledger.release(reservation)

    assert guard.validate(action, observation) == action
    ledger.commit(reserve_action(ledger, action, observation))
    with pytest.raises(ActionBudgetError, match="purchase limit"):
        reserve_action(ledger, action, observation)


@pytest.mark.parametrize(
    ("expected_price", "message"),
    [
        (None, "positive integer"),
        (751, "exceeds maximum"),
        (800, "exceeds maximum"),
    ],
)
def test_purchase_rejects_missing_or_excessive_expected_price(
    expected_price: int | None,
    message: str,
) -> None:
    config = safety_config().model_copy(
        update={
            "allow_skills": ["buy_inspected_shop_item"],
            "max_purchase_price": 750,
            "min_money_after_purchase": 250,
        }
    )
    macros = MacroRegistry({"buy_inspected_shop_item": MacroConfig(actions=[])})
    args: dict[str, str | float | int] = {
        "target_id": "nearby:0",
        "x": 0.316,
        "y": 0.357,
    }
    if expected_price is not None:
        args["expected_price"] = expected_price
    action = SkillAction.model_validate({"name": "buy_inspected_shop_item", "args": args})

    with pytest.raises(SafetyViolation, match=message):
        OperationPolicy(config, macros).validate(action, purchase_observation())


def test_purchase_rejects_insufficient_post_purchase_balance() -> None:
    config = safety_config().model_copy(
        update={
            "allow_skills": ["buy_inspected_shop_item"],
            "max_purchase_price": 750,
            "min_money_after_purchase": 400,
        }
    )
    macros = MacroRegistry({"buy_inspected_shop_item": MacroConfig(actions=[])})
    action = SkillAction.model_validate(
        {
            "name": "buy_inspected_shop_item",
            "args": {
                "target_id": "nearby:0",
                "expected_price": 649,
            },
        }
    )

    with pytest.raises(SafetyViolation, match="minimum is 400"):
        OperationPolicy(config, macros).validate(
            action,
            purchase_observation(include_tooltip=False),
        )


def test_wait_limit() -> None:
    guard = OperationPolicy(safety_config(), MacroRegistry({}))
    observation = Observation(run_id="run", step_index=0, mode="mock")
    assert (
        guard.validate(
            WaitAction(seconds=safety_config().max_wait_seconds),
            observation,
        ).seconds
        == safety_config().max_wait_seconds
    )
    with pytest.raises(SafetyViolation):
        guard.validate(WaitAction(seconds=4), observation)


def trade_in_progress_observation() -> Observation:
    """The r2 trade window: two inventories open, the trader's among them.

    Kenshi runs a trade as the player's inventory beside the trader's, moving
    items across before exiting. Live run
    live-shop-ownership-regression-20260729-r2 sat in exactly that state -
    `open_inventory_windows` 2, the seller's own BARMAN window advertising its
    stock - while `active_screen` collapsed to 'inventory', and the purchase
    guard refused four planner calls in ninety seconds saying the trade screen
    was not open. It was open; the operator could see it.
    """

    observation = generic_purchase_observation()
    assert observation.telemetry is not None
    return observation.model_copy(
        update={
            "telemetry": observation.telemetry.model_copy(
                update={
                    "ui": observation.telemetry.ui.model_copy(
                        update={
                            "active_screen": "inventory",
                            "open_inventory_windows": 2,
                        }
                    )
                }
            )
        }
    )


def test_purchase_survives_a_trade_screen_kenshi_labels_inventory() -> None:
    action = generic_purchase_action()
    observation = trade_in_progress_observation()

    assert (
        OperationPolicy(generic_purchase_config(), MacroRegistry({})).validate(
            action,
            observation,
        )
        == action
    )


def test_purchase_still_refuses_a_solo_inventory_with_no_trader_window() -> None:
    """One window open is our own bag, not a shop. This must stay refused."""

    action = generic_purchase_action()
    observation = trade_in_progress_observation()
    assert observation.telemetry is not None
    solo = observation.model_copy(
        update={
            "telemetry": observation.telemetry.model_copy(
                update={
                    "ui": observation.telemetry.ui.model_copy(update={"open_inventory_windows": 1})
                }
            )
        }
    )

    with pytest.raises(SafetyViolation, match="trade"):
        OperationPolicy(generic_purchase_config(), MacroRegistry({})).validate(action, solo)
