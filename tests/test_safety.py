import pytest

from kenshi_agent.config import MacroConfig, NormalizedPointerBoundsConfig, SafetyConfig
from kenshi_agent.models import (
    ClickAction,
    ControlMode,
    CoordinateSpace,
    GameState,
    KeyAction,
    MoveCursorAction,
    Observation,
    PauseAction,
    ScrollAction,
    SkillAction,
    SkillArgument,
    TelemetrySnapshot,
    UIState,
    WaitAction,
)
from kenshi_agent.safety import ActionGuard, SafetyViolation
from kenshi_agent.skills import MacroRegistry


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
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(run_id="run", step_index=0, mode="mock")
    with pytest.raises(SafetyViolation):
        guard.validate(ClickAction(x=1.1, y=0.5), observation)


def test_stale_live_click_is_blocked() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
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
    guard = ActionGuard(safety_config(), MacroRegistry({}))
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
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="Screen-space"):
        guard.validate(ClickAction(x=100, y=100, space=CoordinateSpace.SCREEN), observation)


def test_live_move_cursor_uses_the_same_bounds_as_clicks() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="outside the Kenshi window"):
        guard.validate(MoveCursorAction(x=1280, y=20, space=CoordinateSpace.CLIENT), observation)


def test_live_client_pointer_requires_known_window_dimensions() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run", step_index=0, mode="live", telemetry=TelemetrySnapshot()
    )
    with pytest.raises(SafetyViolation, match="dimensions are unknown"):
        guard.validate(ClickAction(x=20, y=20, space=CoordinateSpace.CLIENT), observation)


def test_live_pause_requires_known_current_state() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    unknown = Observation(run_id="run", step_index=0, mode="live", telemetry=TelemetrySnapshot())
    with pytest.raises(SafetyViolation, match="pause state is unknown"):
        guard.validate(PauseAction(paused=True), unknown)

    known = unknown.model_copy(
        update={"telemetry": TelemetrySnapshot(game=GameState(paused=False))}
    )
    assert guard.validate(PauseAction(paused=True), known).paused is True

    with pytest.raises(SafetyViolation, match="Direct live unpause"):
        guard.validate(PauseAction(paused=False), known)


def test_safety_pause_bypasses_only_the_rate_budget() -> None:
    config = safety_config().model_copy(update={"max_actions_per_minute": 1})
    guard = ActionGuard(config, MacroRegistry({}))
    observation = Observation(run_id="run", step_index=0, mode="mock")

    guard.validate(PauseAction(paused=True), observation)
    with pytest.raises(SafetyViolation, match="rate limit"):
        guard.validate(PauseAction(paused=True), observation)

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


def test_live_skill_must_be_configured_and_allowlisted() -> None:
    macros = MacroRegistry({"open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}])})
    guard = ActionGuard(safety_config(), macros)
    observation = Observation(run_id="run", step_index=0, mode="live")
    action = guard.validate(SkillAction(name="open_map"), observation)
    assert action.kind == "skill"


def test_interface_only_guard_rejects_native_assisted_skill() -> None:
    config = safety_config().model_copy(
        update={"allow_skills": ["approach_confirmed_vendor"]}
    )
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
        ActionGuard(config, macros, control_mode=ControlMode.INTERFACE_ONLY).validate(
            SkillAction(name="approach_confirmed_vendor"),
            observation,
        )


def test_native_assisted_guard_accepts_marked_skill_only_for_matching_observation() -> None:
    config = safety_config().model_copy(
        update={"allow_skills": ["approach_confirmed_vendor"]}
    )
    macros = MacroRegistry(
        {
            "approach_confirmed_vendor": MacroConfig(
                requires_native_assisted=True,
                actions=[{"kind": "hotkey", "keys": ["ctrl", "shift", "f10"]}],
            )
        }
    )
    guard = ActionGuard(config, macros, control_mode=ControlMode.NATIVE_ASSISTED)
    action = SkillAction(name="approach_confirmed_vendor")

    with pytest.raises(SafetyViolation, match="control mode"):
        guard.validate(action, Observation(run_id="run", step_index=0, mode="live"))

    accepted = guard.validate(
        action,
        Observation(
            run_id="run",
            step_index=0,
            mode="live",
            control_mode=ControlMode.NATIVE_ASSISTED,
        ),
    )
    assert accepted == action


def test_allowlisted_skill_can_expand_to_a_blocked_top_level_primitive() -> None:
    config = safety_config().model_copy(
        update={"allow_action_kinds": ["noop", "stop", "wait", "skill"]}
    )
    macros = MacroRegistry({"open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}])})
    guard = ActionGuard(config, macros)
    observation = Observation(run_id="run", step_index=0, mode="live")

    assert guard.validate(SkillAction(name="open_map"), observation).kind == "skill"
    with pytest.raises(SafetyViolation, match="Action kind 'key'"):
        guard.validate(KeyAction(key="m"), observation)


def test_live_skill_primitives_receive_pointer_validation() -> None:
    macros = MacroRegistry(
        {
            "open_map": MacroConfig(
                actions=[{"kind": "click", "x": 100, "y": 100, "space": "screen"}]
            )
        }
    )
    guard = ActionGuard(safety_config(), macros)
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
    guard = ActionGuard(config, macros)

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
        ActionGuard(config, macros).validate(
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
        ActionGuard(config, macros).validate(action, unpaused)


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
        ActionGuard(config, macros).validate(action, paused)


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
    observation = Observation.model_validate(
        {
            "run_id": "purchase",
            "step_index": 0,
            "mode": "live",
            "telemetry_stale": False,
            "telemetry": {
                "game": {"paused": True, "money": 1000},
                "ui": {"active_screen": "trade"},
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
    action = SkillAction.model_validate(
        {
            "name": "buy_inspected_shop_item",
            "args": {"x": 0.316, "y": 0.357, "expected_price": 649},
        }
    )
    guard = ActionGuard(config, macros)

    assert guard.validate(action, observation) == action
    with pytest.raises(SafetyViolation, match="purchase limit"):
        guard.validate(action, observation)


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
    macros = MacroRegistry(
        {"buy_inspected_shop_item": MacroConfig(actions=[])}
    )
    observation = Observation.model_validate(
        {
            "run_id": "purchase",
            "step_index": 0,
            "mode": "live",
            "telemetry_stale": False,
            "telemetry": {
                "game": {"money": 1000},
                "ui": {"active_screen": "trade"},
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
    args: dict[str, float | int] = {"x": 0.316, "y": 0.357}
    if expected_price is not None:
        args["expected_price"] = expected_price
    action = SkillAction.model_validate(
        {"name": "buy_inspected_shop_item", "args": args}
    )

    with pytest.raises(SafetyViolation, match=message):
        ActionGuard(config, macros).validate(action, observation)


def test_purchase_rejects_insufficient_post_purchase_balance() -> None:
    config = safety_config().model_copy(
        update={
            "allow_skills": ["buy_inspected_shop_item"],
            "max_purchase_price": 750,
            "min_money_after_purchase": 400,
        }
    )
    macros = MacroRegistry(
        {"buy_inspected_shop_item": MacroConfig(actions=[])}
    )
    observation = Observation.model_validate(
        {
            "run_id": "purchase",
            "step_index": 0,
            "mode": "live",
            "telemetry": {
                "game": {"money": 1000},
                "ui": {"active_screen": "trade"},
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
    action = SkillAction.model_validate(
        {
            "name": "buy_inspected_shop_item",
            "args": {"expected_price": 649},
        }
    )

    with pytest.raises(SafetyViolation, match="minimum is 400"):
        ActionGuard(config, macros).validate(action, observation)


def test_wait_limit() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(run_id="run", step_index=0, mode="mock")
    with pytest.raises(SafetyViolation):
        guard.validate(WaitAction(seconds=4), observation)
