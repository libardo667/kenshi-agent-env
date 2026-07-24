"""Generic live-continuous policy for composable semantic actions.

`food_procurement_v1` is a recipe: it knows the exact phases, the exact skill
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

from .action_contracts import ActionContract, contract_for
from .models import (
    Action,
    ConditionKind,
    ControlMode,
    IdempotencyPolicy,
    Observation,
    PlanEnvelope,
    PlanStep,
    is_controller_primitive,
)

DIALOGUE_INTERACTION_MAX_STEPS = 4

# Conditions that can only be settled by a later world revision. A plan whose
# success is judged solely by, say, control_mode would "succeed" without the
# game ever changing, so at least one causal check is required per step.
_CAUSAL_CONDITION_PREFIXES = ("telemetry.", "selected.", "target.")


def _is_causal_condition(kind: ConditionKind, path: str | None) -> bool:
    if kind is ConditionKind.TELEMETRY_FRESH:
        return False
    if path is None:
        return False
    return path.startswith(_CAUSAL_CONDITION_PREFIXES)


def _step_action_errors(
    step: PlanStep,
    observation: Observation,
    *,
    control_mode: ControlMode,
) -> list[str]:
    errors: list[str] = []
    action: Action = step.action
    label = f"step {step.step_id!r}"

    if is_controller_primitive(action):
        errors.append(
            f"{label} authors raw controller primitive {action.kind!r}; the generic "
            "surface accepts semantic actions only, because a bare coordinate "
            "carries no evidence about what it would activate"
        )
        return errors

    contract: ActionContract | None = contract_for(action)
    if contract is None:
        errors.append(
            f"{label} action {action.kind!r} has no authoritative action contract"
        )
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

    binding = contract.bind(action, observation)
    if not binding.bound:
        errors.append(f"{label} reference does not bind to current state: {binding.reason}")

    if step.idempotency is not contract.idempotency:
        errors.append(
            f"{label} declares idempotency {step.idempotency.value!r} but the contract "
            f"for {action.kind!r} requires {contract.idempotency.value!r}"
        )
    if step.retry_budget and contract.idempotency is IdempotencyPolicy.AT_MOST_ONCE:
        errors.append(
            f"{label} retries an at-most-once action; a delayed confirmation is not "
            "permission to act twice"
        )

    if not any(
        _is_causal_condition(condition.kind, condition.path)
        for condition in step.success_conditions
    ):
        errors.append(
            f"{label} has no causal success condition; success must be observable in a "
            "later world revision rather than assumed from dispatch"
        )
    return errors


def dialogue_interaction_policy_errors(
    plan: PlanEnvelope,
    observation: Observation,
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

    if len(plan.steps) > DIALOGUE_INTERACTION_MAX_STEPS:
        errors.append(
            f"plan has {len(plan.steps)} steps; the generic interaction policy allows at "
            f"most {DIALOGUE_INTERACTION_MAX_STEPS}"
        )

    if not any(
        condition.kind is ConditionKind.TELEMETRY_FRESH for condition in plan.assumptions
    ):
        errors.append("plan must assume telemetry freshness")

    for step in plan.steps:
        errors.extend(
            _step_action_errors(step, observation, control_mode=plan.control_mode)
        )

    # Risk budgets must cover what the contracts actually cost, so an
    # underdeclared budget cannot smuggle a native or pointer action through.
    pointer = purchase = native = 0
    for step in plan.steps:
        contract = contract_for(step.action)
        if contract is None:
            continue
        attempts = 1 + step.retry_budget
        pointer += contract.risk.pointer_actions * attempts
        purchase += contract.risk.purchase_actions * attempts
        native += contract.risk.native_assisted_actions * attempts
    if pointer > plan.risk_budget.max_pointer_actions:
        errors.append(
            f"plan contract pointer cost {pointer} exceeds its declared pointer budget "
            f"{plan.risk_budget.max_pointer_actions}"
        )
    if purchase > plan.risk_budget.max_purchase_actions:
        errors.append(
            f"plan contract purchase cost {purchase} exceeds its declared purchase budget "
            f"{plan.risk_budget.max_purchase_actions}"
        )
    if native > plan.risk_budget.max_native_assisted_actions:
        errors.append(
            f"plan contract native-assisted cost {native} exceeds its declared "
            f"native-assisted budget {plan.risk_budget.max_native_assisted_actions}"
        )

    return errors
