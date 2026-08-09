"""Pure evaluation of declared conditions against one observation."""

from __future__ import annotations

from .core.observation import Observation
from .core.planning import (
    Condition,
    ConditionEvaluation,
    ConditionKind,
    ConditionOperator,
    ConditionResult,
)
from .core.telemetry import CharacterState
from .core.world import WorldStateRevision

_NATIVE_APPROACH_CAPABILITIES = (
    "control.approach_dialogue_target",
    "control.approach_vendor",
)
_NATIVE_CONTROL_CAPABILITIES = (
    *_NATIVE_APPROACH_CAPABILITIES,
    "control.move_to_character",
    "control.select_squad_member",
    "control.regroup_with_squad_member",
    "control.move_in_direction",
    "control.travel_to_map_destination",
    "control.exit_current_building",
    "control.perform_context_action",
    "control.produce_resource_output",
)

# Capability names that mean the same thing. The plug-in still emits the
# vendor-era name for what is really "may issue a pathing order to a valid
# dialogue target", so a condition naming either must be satisfied by either -
# otherwise the generic vocabulary is unusable until every DLL is rebuilt.
_CAPABILITY_ALIASES: dict[str, tuple[str, ...]] = {
    name: _NATIVE_APPROACH_CAPABILITIES for name in _NATIVE_APPROACH_CAPABILITIES
}


def capability_satisfied(name: str, available: set[str] | frozenset[str]) -> bool:
    """Whether an advertised capability set satisfies a required name."""

    if name in available:
        return True
    return any(alias in available for alias in _CAPABILITY_ALIASES.get(name, ()))


_PATH_CAPABILITY_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "telemetry.identity_session_id": ("identity.stable_handles",),
    "telemetry.game.loaded": ("game.pause",),
    "telemetry.game.paused": ("game.pause",),
    "telemetry.game.speed_multiplier": ("game.speed",),
    "telemetry.game.elapsed_minutes": ("game.time",),
    "telemetry.game.money": ("game.money",),
    "telemetry.game.location_name": ("game.location",),
    "telemetry.game.day": ("game.time",),
    "telemetry.game.hour": ("game.time",),
    "telemetry.game.minute": ("game.time",),
    "telemetry.ui.active_screen": ("ui.inventory", "ui.dialogue"),
    "telemetry.ui.modal_open": ("ui.inventory", "ui.dialogue"),
    "telemetry.ui.dialogue_open": ("ui.dialogue",),
    "telemetry.ui.dialogue_target_id": ("ui.dialogue.target",),
    "telemetry.ui.dialogue_option_count": ("ui.dialogue.options",),
    "telemetry.ui.dialogue_option_0": ("ui.dialogue.options",),
    "telemetry.ui.visible_control_count": ("ui.visible_controls",),
    # Screen-state signals ride with the inventory/UI capability that reports them.
    "telemetry.ui.stats_window_open": ("ui.inventory", "ui.dialogue"),
    "telemetry.ui.open_inventory_windows": ("ui.inventory", "ui.dialogue"),
    "telemetry.ui.management_screen_open": ("ui.inventory", "ui.dialogue"),
    "telemetry.ui.management_tab": ("ui.inventory", "ui.dialogue"),
    "telemetry.ui.tooltip_visible": ("ui.tooltip",),
    "telemetry.ui.tooltip_text": ("ui.tooltip",),
    "telemetry.ui.context_menu_open": ("ui.inventory", "ui.dialogue"),
    "telemetry.primary_character_id": ("roster.basic",),
    "telemetry.selected_character_count": ("selection.complete",),
    "telemetry.active_shop_trader_count": ("nearby.shop_owners",),
    # These are the shared command-channel fields, so any reviewed native
    # command capability makes them authoritative. Restricting them to the
    # first approach command made a direction's own terminal result impossible
    # to use as a postcondition.
    "telemetry.native_control.available": _NATIVE_CONTROL_CAPABILITIES,
    "telemetry.native_control.command_active": _NATIVE_CONTROL_CAPABILITIES,
    "telemetry.native_control.last_command_sequence": _NATIVE_CONTROL_CAPABILITIES,
    "telemetry.native_control.last_command": _NATIVE_CONTROL_CAPABILITIES,
    "telemetry.native_control.last_result": _NATIVE_CONTROL_CAPABILITIES,
    "telemetry.native_control.last_target": _NATIVE_CONTROL_CAPABILITIES,
    "telemetry.native_control.last_target_id": _NATIVE_CONTROL_CAPABILITIES,
    "selected.alive": ("roster.basic",),
    "selected.conscious": ("roster.basic",),
    "selected.down": ("roster.basic",),
    "selected.in_combat": ("roster.basic",),
    "selected.position.x": ("roster.basic",),
    "selected.position.y": ("roster.basic",),
    "selected.position.z": ("roster.basic",),
    "selected.movement_speed": ("roster.basic",),
    "selected.indoors": ("roster.indoors",),
    "selected.nutrition_reserve": ("roster.hunger",),
    "selected.bleeding_rate": ("roster.health",),
    "selected.food_items": ("roster.hunger", "roster.inventory", "roster.basic"),
    "selected.first_aid_kits": ("roster.inventory",),
    "selected.current_goal": ("roster.current_goal",),
    "target.disposition": ("nearby.characters", "nearby.visible_entities"),
    "target.distance": ("nearby.characters", "nearby.visible_entities"),
    "target.visible": ("nearby.characters", "nearby.visible_entities"),
    "target.conscious": ("nearby.characters", "nearby.visible_entities"),
    "target.has_vendor_list": ("nearby.roles",),
    "target.is_squad_leader": ("nearby.roles",),
    "target.has_dialogue": ("nearby.roles",),
    "target.shop_inventory_owner": ("nearby.shop_owners",),
}


def _selected_character(observation: Observation) -> CharacterState | None:
    telemetry = observation.telemetry
    if telemetry is None:
        return None
    selected_id = telemetry.primary_character_id
    if selected_id is not None:
        selected = next(
            (character for character in telemetry.roster if character.id == selected_id),
            None,
        )
        if selected is not None:
            return selected
    # Without an exported primary, only an unambiguous selection can stand
    # in for one. The old fallback returned the first `selected` character
    # of several - which is roster order, not Kenshi's primary - and then,
    # if none were selected at all, `roster[0]`, who need not be selected in
    # any sense. Facts about "the selected character" were answered about
    # somebody else and reported as true.
    selected_members = telemetry.selected_characters()
    if len(selected_members) == 1:
        return selected_members[0]
    return None


def _resolve_field(condition: Condition, observation: Observation) -> object | None:
    telemetry = observation.telemetry
    path = condition.path
    if path == "control_mode":
        return observation.control_mode.value
    if path == "telemetry_stale":
        return observation.telemetry_stale
    if telemetry is None:
        return None
    assert path is not None  # pragma: no mutate - field conditions always have a path

    direct_paths: dict[str, object | None] = {
        "telemetry.identity_session_id": telemetry.identity_session_id,
        "telemetry.game.loaded": telemetry.game.loaded,
        "telemetry.game.paused": telemetry.game.paused,
        "telemetry.game.speed_multiplier": telemetry.game.speed_multiplier,
        "telemetry.game.elapsed_minutes": telemetry.game.elapsed_minutes,
        "telemetry.game.money": telemetry.game.money,
        "telemetry.game.location_name": telemetry.game.location_name,
        "telemetry.game.day": telemetry.game.day,
        "telemetry.game.hour": telemetry.game.hour,
        "telemetry.game.minute": telemetry.game.minute,
        "telemetry.ui.active_screen": telemetry.ui.active_screen,
        "telemetry.ui.modal_open": telemetry.ui.modal_open,
        "telemetry.ui.dialogue_open": telemetry.ui.dialogue_open,
        "telemetry.ui.dialogue_target_id": telemetry.ui.dialogue_target_id,
        "telemetry.ui.dialogue_option_count": (
            len(telemetry.ui.dialogue_options)
            if telemetry.ui.dialogue_options is not None
            else None
        ),
        "telemetry.ui.dialogue_option_0": (
            telemetry.ui.dialogue_options[0] if telemetry.ui.dialogue_options else None
        ),
        "telemetry.ui.visible_control_count": (
            len(telemetry.ui.visible_controls)
            if telemetry.ui.visible_controls is not None
            else None
        ),
        "telemetry.ui.stats_window_open": telemetry.ui.stats_window_open,
        "telemetry.ui.open_inventory_windows": telemetry.ui.open_inventory_windows,
        "telemetry.ui.management_screen_open": telemetry.ui.management_screen_open,
        "telemetry.ui.management_tab": telemetry.ui.management_tab,
        "telemetry.ui.tooltip_visible": telemetry.ui.tooltip_visible,
        "telemetry.ui.tooltip_text": telemetry.ui.tooltip_text,
        "telemetry.ui.context_menu_open": telemetry.ui.context_menu_open,
        "telemetry.primary_character_id": telemetry.primary_character_id,
        "telemetry.selected_character_count": len(telemetry.selected_character_ids),
        "telemetry.active_shop_trader_count": telemetry.active_shop_trader_count,
        "telemetry.native_control.available": telemetry.native_control.available,
        "telemetry.native_control.command_active": (
            telemetry.native_control.active_command_id is not None
        ),
        "telemetry.native_control.last_command_sequence": (
            telemetry.native_control.last_command_sequence
        ),
        "telemetry.native_control.last_command": telemetry.native_control.last_command,
        "telemetry.native_control.last_result": telemetry.native_control.last_result,
        "telemetry.native_control.last_target": telemetry.native_control.last_target,
        "telemetry.native_control.last_target_id": telemetry.native_control.last_target_id,
    }
    if path in direct_paths:
        return direct_paths[path]

    if path.startswith("selected."):
        selected = _selected_character(observation)
        if selected is None:
            return None
        selected_paths = {
            "selected.alive": selected.alive,
            "selected.conscious": selected.conscious,
            "selected.down": selected.down,
            "selected.in_combat": selected.in_combat,
            "selected.movement_speed": selected.movement_speed,
            "selected.indoors": selected.indoors,
            "selected.nutrition_reserve": selected.hunger,
            "selected.bleeding_rate": selected.bleeding_rate,
            "selected.food_items": selected.food_items,
            "selected.first_aid_kits": selected.first_aid_kits,
            "selected.current_goal": selected.current_goal,
            "selected.position.x": (selected.position.x if selected.position is not None else None),
            "selected.position.y": (selected.position.y if selected.position is not None else None),
            "selected.position.z": (selected.position.z if selected.position is not None else None),
        }
        return selected_paths[path]

    if path.startswith("target."):
        target = next(
            (entity for entity in telemetry.nearby_entities if entity.id == condition.target_id),
            None,
        )
        if target is None:
            return None
        target_paths = {
            "target.disposition": target.disposition.value,
            "target.distance": target.distance,
            "target.visible": target.visible,
            "target.conscious": target.conscious,
            "target.has_vendor_list": target.has_vendor_list,
            "target.is_squad_leader": target.is_squad_leader,
            "target.has_dialogue": target.has_dialogue,
            "target.shop_inventory_owner": target.shop_inventory_owner,
        }
        return target_paths[path]
    return None


def _is_telemetry_condition(condition: Condition) -> bool:
    return bool(
        condition.kind in {ConditionKind.CAPABILITY, ConditionKind.TELEMETRY_FRESH}
        or condition.required_capabilities
        or (
            condition.path is not None
            and (
                condition.path.startswith("telemetry.")
                or condition.path.startswith("selected.")
                or condition.path.startswith("target.")
            )
        )
    )


def _evaluation(
    condition: Condition,
    result: ConditionResult,
    reason: str,
    *,
    actual: object | None = None,
) -> ConditionEvaluation:
    scalar = actual if isinstance(actual, (str, int, float, bool)) else None
    return ConditionEvaluation(
        condition=condition,
        result=result,
        actual=scalar,
        reason=reason,
    )


def evaluate_condition(
    condition: Condition,
    observation: Observation,
    *,
    after_revision: WorldStateRevision | None = None,
) -> ConditionEvaluation:
    telemetry_condition = _is_telemetry_condition(condition)
    if after_revision is not None:
        if telemetry_condition:
            current_sequence = observation.world_revision.telemetry_sequence
            prior_sequence = after_revision.telemetry_sequence
            if (
                current_sequence is None
                or prior_sequence is None
                or current_sequence <= prior_sequence
            ):
                return _evaluation(
                    condition,
                    ConditionResult.STALE,
                    (
                        "No later telemetry revision exists "  # mutation: diagnostic-only
                        "after the action start."
                    ),
                )
        elif not observation.world_revision.is_later_than(after_revision):
            return _evaluation(
                condition,
                ConditionResult.STALE,
                (
                    "No later world revision exists "  # mutation: diagnostic-only
                    "after the action start."
                ),
            )

    if telemetry_condition:
        if observation.telemetry is None:
            return _evaluation(
                condition,
                ConditionResult.UNAVAILABLE,
                "Telemetry is unavailable.",  # mutation: diagnostic-only
            )
        if observation.telemetry_stale:
            return _evaluation(
                condition,
                ConditionResult.STALE,
                "Telemetry is marked stale.",  # mutation: diagnostic-only
            )
        age = observation.telemetry_age_seconds
        if age is not None and age > condition.max_age_seconds:
            return _evaluation(
                condition,
                ConditionResult.STALE,
                (
                    f"Telemetry age {age:.3f}s exceeds "  # mutation: diagnostic-only
                    f"{condition.max_age_seconds:.3f}s."
                ),
            )
        available = set(observation.telemetry.capabilities)
        missing = sorted(
            name
            for name in set(condition.required_capabilities)
            if not capability_satisfied(name, available)
        )
        if missing:
            return _evaluation(
                condition,
                ConditionResult.UNAVAILABLE,
                (
                    "Kenshi is not currently reporting "  # mutation: diagnostic-only
                    f"these capabilities: {missing}."
                ),
            )
        alternatives = (
            _PATH_CAPABILITY_ALTERNATIVES.get(condition.path)
            if condition.path is not None
            else None
        )
        if alternatives is not None and not any(
            capability in observation.telemetry.capabilities for capability in alternatives
        ):
            return _evaluation(
                condition,
                ConditionResult.UNAVAILABLE,
                (
                    "The field's authoritative capability is "  # mutation: diagnostic-only
                    f"unavailable; expected one of {list(alternatives)}."
                ),
            )

    if condition.kind == ConditionKind.TELEMETRY_FRESH:
        if observation.telemetry_age_seconds is None:
            return _evaluation(
                condition,
                ConditionResult.UNKNOWN,
                "Telemetry age is unknown.",  # mutation: diagnostic-only
            )
        actual: object = not observation.telemetry_stale
    elif condition.kind == ConditionKind.CAPABILITY:
        telemetry = observation.telemetry
        # Capability conditions are telemetry conditions, so the availability
        # fence above already proved this before control can reach here.
        assert telemetry is not None  # pragma: no mutate
        assert condition.path is not None  # pragma: no mutate
        actual = capability_satisfied(condition.path, set(telemetry.capabilities))
    else:
        actual = _resolve_field(condition, observation)

    if actual is None:
        return _evaluation(
            condition,
            ConditionResult.UNKNOWN,
            (
                "Condition value for "  # mutation: diagnostic-only
                f"{condition.path or condition.kind.value!r} is unknown."
            ),
        )

    if condition.operator == ConditionOperator.EQUALS:
        result = ConditionResult.TRUE if actual == condition.expected else ConditionResult.FALSE
    elif condition.operator == ConditionOperator.NOT_EQUALS:
        result = ConditionResult.TRUE if actual != condition.expected else ConditionResult.FALSE
    elif condition.operator == ConditionOperator.CONTAINS:
        if not isinstance(actual, str) or not isinstance(condition.expected, str):
            return _evaluation(
                condition,
                ConditionResult.UNKNOWN,
                (
                    "Contains comparison requires observed "  # mutation: diagnostic-only
                    "and expected string values."
                ),
                actual=actual,
            )
        result = ConditionResult.TRUE if condition.expected in actual else ConditionResult.FALSE
    elif (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(condition.expected, (int, float))
        and not isinstance(condition.expected, bool)
    ):
        passed = {
            ConditionOperator.LESS_THAN: actual < condition.expected,
            ConditionOperator.LESS_THAN_OR_EQUAL: actual <= condition.expected,
            ConditionOperator.GREATER_THAN: actual > condition.expected,
            ConditionOperator.GREATER_THAN_OR_EQUAL: actual >= condition.expected,
        }[condition.operator]
        result = ConditionResult.TRUE if passed else ConditionResult.FALSE
    else:
        return _evaluation(
            condition,
            ConditionResult.UNKNOWN,
            "Ordered comparison requires observed numeric values.",  # mutation: diagnostic-only
            actual=actual,
        )
    return _evaluation(
        condition,
        result,
        (
            f"Observed {actual!r}; expected "  # mutation: diagnostic-only
            f"{condition.operator.value} {condition.expected!r}."
        ),
        actual=actual,
    )


def evaluate_conditions(
    conditions: list[Condition],
    observation: Observation,
    *,
    after_revision: WorldStateRevision | None = None,
) -> list[ConditionEvaluation]:
    return [
        evaluate_condition(
            condition,
            observation,
            after_revision=after_revision,
        )
        for condition in conditions
    ]
