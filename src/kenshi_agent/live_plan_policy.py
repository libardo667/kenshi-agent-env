"""Generic live-continuous policy for composable semantic actions.

The policy this replaced was a recipe: it knew the exact phases, the exact skill
order, and the exact sentence a Barman says. That made one calibrated chain safe
and every other chain impossible.

This policy validates *properties* instead of a script. It asks whether each
action has an authoritative contract, whether its arguments bind to something
the current observation actually advertises, whether the plan stays inside its
declared budgets, and whether success is stated causally. It deliberately does
not know what a good plan looks like: it never requires a particular step order,
never injects a missing step, and never mentions a scenario, a role, a label, or
a coordinate. A planner that composes approach-then-activate and a planner that
composes activate-alone are both acceptable if their references bind.
"""

from __future__ import annotations

from .action_contracts import (
    ActionContract,
    CompletionOwner,
    completion_contract_for,
    contract_for,
)
from .models import (
    TIME_GAME_BINDINGS,
    TOGGLE_GAME_BINDINGS,
    Action,
    ConditionKind,
    ConditionResult,
    ControlMode,
    IdempotencyPolicy,
    Observation,
    PauseAction,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
    SetSpeedAction,
    UseGameBindingAction,
    is_controller_primitive,
    is_planner_control_action,
)
from .planning import evaluate_conditions

# Default only. The caller passes the configured `max_plan_steps` so a
# long-form run can be given a longer leash without editing this module.
LIVE_PLAN_MAX_STEPS = 4

# Conditions that can only be settled by a later world revision. A plan whose
# success is judged solely by, say, control_mode would "succeed" without the
# game ever changing, so at least one causal check is required per step.
#
# `camera.` belongs here: the camera's position is observed world state that a
# later revision reports differently once it moves. Leaving it out made every
# camera step unprovable - the only field that records the effect did not count
# as evidence of it - so panning was rejected however it was written. On a
# streamed run that is not a small thing: the camera is what anyone watching
# actually sees.
_CAUSAL_CONDITION_PREFIXES = ("telemetry.", "selected.", "target.", "camera.")


def _is_causal_condition(kind: ConditionKind, path: str | None) -> bool:
    if kind is not ConditionKind.FIELD:
        return False
    if path is None:
        return False
    return path.startswith(_CAUSAL_CONDITION_PREFIXES)


def _step_action_errors(
    step: PlanStep,
    observation: Observation,
    *,
    control_mode: ControlMode,
    require_binding: bool,
) -> list[str]:
    errors: list[str] = []
    action: Action = step.action
    label = f"step {step.step_id!r}"

    if require_binding:
        failure_evaluations = evaluate_conditions(
            step.failure_conditions,
            observation,
        )
        for evaluation in failure_evaluations:
            if evaluation.result is ConditionResult.TRUE:
                errors.append(
                    f"{label} failure condition is already true before dispatch: "
                    f"{evaluation.reason}"
                )
            elif evaluation.result is ConditionResult.UNKNOWN:
                errors.append(
                    f"{label} failure condition is not observable before dispatch: "
                    f"{evaluation.reason}"
                )

    if is_controller_primitive(action):
        errors.append(
            f"{label} authors raw controller primitive {action.kind!r}; the generic "
            "surface accepts semantic actions only, because a bare coordinate "
            "carries no evidence about what it would activate"
        )
        return errors

    direct_unpause = (
        isinstance(action, PauseAction)
        and action.paused is False
    ) or (
        isinstance(action, SetSpeedAction)
        and observation.telemetry is not None
        and observation.telemetry.game.paused is True
    )
    if direct_unpause:
        errors.append(
            f"{label} requests direct live unpause, which the action guard cannot "
            "authorize. Do not add an unpause step before movement: "
            "approach_dialogue_target, move_to_character, move_in_direction, "
            "travel_to_map_destination, harvest_resource, and "
            "respond_to_immediate_threat own any world-time "
            "transition they require."
        )
        return errors

    if (
        isinstance(action, UseGameBindingAction)
        and action.binding in TIME_GAME_BINDINGS
    ):
        errors.append(
            f"{label} authors raw time binding {action.binding.value!r}; state a "
            "semantic gameplay intention and let its runtime option own playback"
        )
        return errors

    if is_planner_control_action(action):
        # Run control (stop, noop, wait, pause, set_speed) touches no game object
        # and binds to no reference. Its completion is runtime-owned rather than
        # a second model-authored description of the same intention.
        completion = completion_contract_for(action, observation)
        if (
            completion.owner is CompletionOwner.RUNTIME_CONDITIONS
            and not completion.conditions
        ):
            errors.append(
                f"{label} runtime completion baseline is unavailable; no input "
                "may be dispatched without a verifiable terminal"
            )
        return errors

    contract: ActionContract | None = contract_for(action)
    if contract is None:
        errors.append(f"{label} action {action.kind!r} has no authoritative action contract")
        return errors
    if not contract.planner_visible:
        errors.append(f"{label} action {action.kind!r} is not planner-visible")
        return errors

    if not contract.allows_control_mode(control_mode):
        errors.append(
            f"{label} action {action.kind!r} is not permitted in control mode "
            f"{control_mode.value!r}"
        )

    capabilities = set(
        observation.telemetry.capabilities if observation.telemetry is not None else []
    )
    missing = contract.missing_capabilities(capabilities)
    if missing:
        errors.append(
            f"{label} action {action.kind!r} requires unavailable capabilities: "
            + ", ".join(missing)
        )

    # Only the step about to run must bind right now. A later step legitimately
    # refers to state its predecessors will create - a closing dialogue reply
    # cannot bind before the approach has opened one - and demanding otherwise
    # would reject every genuinely composed plan. Each step is still bound and
    # revalidated when it is actually reached, and again inside the input lease.
    if require_binding:
        binding = contract.bind(action, observation)
        if not binding.bound:
            errors.append(f"{label} reference does not bind to current state: {binding.reason}")

    # Only a claim *weaker* than the contract is a problem. Declaring
    # `at_most_once` for an action the contract says is safe to retry is simply
    # more cautious, and rejecting it trapped the planner in a loop it could not
    # escape: everything else in the prompt tells it to prefer at_most_once.
    if (
        contract.idempotency is IdempotencyPolicy.AT_MOST_ONCE
        and step.idempotency is IdempotencyPolicy.SAFE_TO_RETRY
    ):
        errors.append(
            f"{label} declares idempotency {step.idempotency.value!r}, but "
            f"{action.kind!r} is {contract.idempotency.value!r} and may not be retried"
        )
    if step.retry_budget and isinstance(action, UseGameBindingAction):
        # Retryability here is a property of the individual binding, not the
        # action kind. Panning the camera means pressing the same key several
        # times and is exactly what a retry is for; pressing `toggle_inventory`
        # twice closes the window the first press opened.
        if action.binding in TOGGLE_GAME_BINDINGS:
            errors.append(
                f"{label} retries {action.binding.value!r}, which toggles: a second "
                "press undoes the first rather than repeating it"
            )
    elif step.retry_budget and contract.idempotency is IdempotencyPolicy.AT_MOST_ONCE:
        errors.append(
            f"{label} retries an at-most-once action; a delayed confirmation is not "
            "permission to act twice"
        )

    completion = completion_contract_for(action, observation)
    if (
        completion.owner is CompletionOwner.RUNTIME_CONDITIONS
        and not completion.conditions
    ):
        errors.append(
            f"{label} runtime completion baseline is unavailable; no input may "
            "be dispatched without a verifiable terminal"
        )
    elif completion.owner is CompletionOwner.RUNTIME_CONDITIONS and not all(
        _is_causal_condition(condition.kind, condition.path)
        for condition in completion.conditions
    ):
        errors.append(
            f"{label} runtime completion contract contains a non-causal condition"
        )
    elif completion.owner is CompletionOwner.PLANNER_CONDITIONS and not any(
        _is_causal_condition(condition.kind, condition.path)
        for condition in step.success_conditions
    ):
        errors.append(
            f"{label} has no causal success condition; success must be observable in a "
            "later world revision rather than assumed from dispatch"
        )
    return errors


def live_plan_rebase_errors(
    plan: PlanEnvelope,
    planner_observation: Observation,
    current_observation: Observation,
) -> list[str]:
    """Every reason a plan that aged during planning may not be rebased.

    A hosted strategic call takes tens of seconds while telemetry advances every
    half second, so a returned plan is essentially always stale by sequence
    number. Refusing on that alone would make composition impossible while
    proving nothing: the sequence is not what authorized the plan.

    What authorized it is the contracts' reference bindings plus the plan's own
    typed conditions. So a rebase is permitted exactly when each action still
    binds to the same reference it bound to when the plan was written, the
    assumptions still hold, and control mode and capabilities have not changed.
    Anything else — a target that moved out of the valid set, a control that
    became ambiguous, a withdrawn capability, an unpause — refuses, and the
    executor still revalidates preconditions before dispatch and again inside
    the input lease.
    """

    errors: list[str] = []
    if not plan.based_on_revision.same_snapshot_as(planner_observation.world_revision):
        errors.append("plan basis does not match its immutable planner snapshot")
    if not current_observation.world_revision.is_later_than(planner_observation.world_revision):
        errors.append("current world revision is not causally later than the planner snapshot")

    if current_observation.telemetry is None:
        errors.append("current observation has no telemetry to rebase against")
        return errors
    if current_observation.telemetry_stale:
        errors.append("current telemetry is stale, so the plan cannot be rebased")

    if current_observation.control_mode != planner_observation.control_mode:
        errors.append("control mode changed while the strategic planner was running")

    before_capabilities = set(
        planner_observation.telemetry.capabilities
        if planner_observation.telemetry is not None
        else []
    )
    withdrawn = sorted(before_capabilities - set(current_observation.telemetry.capabilities))
    if withdrawn:
        errors.append("capabilities were withdrawn during planning: " + ", ".join(withdrawn))

    blocking = [
        event
        for event in current_observation.events
        if event in ("human_input_detected", "emergency_stop_detected")
    ]
    if blocking:
        errors.append(f"input authority was withdrawn during planning by {blocking[0]!r}")

    # Only the entry step has to still bind: later steps refer to state their
    # predecessors will create, and each is rebound when it is actually reached.
    entry = next(
        (step for step in plan.steps if step.step_id == plan.entry_step_id),
        None,
    )
    if entry is not None and not is_planner_control_action(entry.action):
        contract = contract_for(entry.action)
        if contract is None:
            errors.append(
                f"step {entry.step_id!r} has no contract, so its reference cannot be rebased"
            )
        else:
            current = contract.bind(entry.action, current_observation)
            if not current.bound:
                errors.append(
                    f"step {entry.step_id!r} pointed at something that changed while the "
                    f"planner was thinking: {current.reason}"
                )

    assumptions = evaluate_conditions(plan.assumptions, current_observation)
    blocked = [
        evaluation for evaluation in assumptions if evaluation.result is not ConditionResult.TRUE
    ]
    if blocked:
        errors.append(
            "the plan's own assumptions stopped being true while the planner was "
            "thinking: " + "; ".join(f"{item.result.value}: {item.reason}" for item in blocked)
        )
    return errors


def plan_contract_costs(plan: PlanEnvelope) -> tuple[int, int, int]:
    """What this plan's steps cost in pointer, purchase and native actions."""
    pointer = purchase = native = 0
    for step in plan.steps:
        contract = contract_for(step.action)
        if contract is None:
            continue
        attempts = 1 + step.retry_budget
        risk = contract.risk_for(step.action)
        pointer += risk.pointer_actions * attempts
        purchase += risk.purchase_actions * attempts
        native += risk.native_assisted_actions * attempts
    return pointer, purchase, native


def with_covering_risk_budget(plan: PlanEnvelope) -> PlanEnvelope:
    """Raise a plan's declared risk budget to cover its own steps.

    The budget was a number the planner had to state and the validator then
    recomputed in order to reject any disagreement — so a plan that plainly said
    "buy this one thing" was thrown away for not also saying "and I intend to
    buy once", at thirty seconds a round trip. The steps are the declaration.

    Only ever raised, never lowered: a planner asking for more headroom than
    this plan spends is stating intent across the patches that may follow, and
    that is its call. The real ceilings are the configured ones, which this does
    not touch, so nothing is smuggled past a limit that was actually protecting
    something.
    """

    pointer, purchase, native = plan_contract_costs(plan)
    budget = plan.risk_budget
    covering = RiskBudget(
        max_pointer_actions=max(budget.max_pointer_actions, pointer),
        max_purchase_actions=max(budget.max_purchase_actions, purchase),
        max_native_assisted_actions=max(budget.max_native_assisted_actions, native),
    )
    if covering == budget:
        return plan
    return plan.model_copy(update={"risk_budget": covering})


def live_plan_policy_errors(
    plan: PlanEnvelope,
    observation: Observation,
    *,
    max_steps: int = LIVE_PLAN_MAX_STEPS,
) -> list[str]:
    """Every reason this plan may not run under the generic interaction policy.

    Returns an empty list when the plan is acceptable. The checks are properties
    of contracts, references, and budgets — never a required action sequence.
    """

    errors: list[str] = []

    if observation.telemetry is None:
        errors.append("generic interaction policy requires current telemetry")
        return errors
    if observation.telemetry_stale:
        errors.append("generic interaction policy requires fresh telemetry")

    if len(plan.steps) > max_steps:
        errors.append(
            f"plan has {len(plan.steps)} steps; the generic interaction policy allows at "
            f"most {max_steps}"
        )

    if not any(condition.kind is ConditionKind.TELEMETRY_FRESH for condition in plan.assumptions):
        errors.append(
            "the plan has no freshness assumption, so nothing establishes that the "
            "world it was built from is still current. Add one entry to "
            '`assumptions`: {"kind": "telemetry_fresh", "operator": "equals", '
            '"expected": true, "max_age_seconds": 3.0}'
        )

    for step in plan.steps:
        errors.extend(
            _step_action_errors(
                step,
                observation,
                control_mode=plan.control_mode,
                require_binding=step.step_id == plan.entry_step_id,
            )
        )

    # Risk budgets must cover what the contracts actually cost, so an
    # underdeclared budget cannot smuggle a native or pointer action through.
    pointer, purchase, native = plan_contract_costs(plan)
    if pointer > plan.risk_budget.max_pointer_actions:
        errors.append(
            f"plan contract pointer cost {pointer} exceeds its declared pointer budget "
            f"{plan.risk_budget.max_pointer_actions}"
        )
    if purchase > plan.risk_budget.max_purchase_actions:
        errors.append(
            f"the plan buys {purchase} time(s) but declares a purchase budget of "
            f"{plan.risk_budget.max_purchase_actions}. A plan has to declare what it "
            f"intends to spend before it spends it: set "
            f"`risk_budget.max_purchase_actions` to {purchase}"
        )
    if native > plan.risk_budget.max_native_assisted_actions:
        errors.append(
            f"plan contract native-assisted cost {native} exceeds its declared "
            f"native-assisted budget {plan.risk_budget.max_native_assisted_actions}"
        )

    return errors
