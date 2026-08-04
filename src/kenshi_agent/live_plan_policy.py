"""Structural policy for composable live plans.

This layer validates the plan's own shape: semantic rather than primitive
steps, coherent retry declarations, causal authored terminals, and declared
risk coverage. Current binding, capability, selection, control mode, and domain
eligibility belong to operation definitions and ``OperationAuthority``.
"""

from __future__ import annotations

from .condition_evaluation import evaluate_conditions
from .core.observation import Observation
from .core.operation import is_controller_primitive
from .core.planning import (
    ConditionKind,
    ConditionResult,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
)
from .operation_definitions import risk_for_operation

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
) -> list[str]:
    """Validate only the structure authored around one plan step.

    Current capability, binding, selection, control-mode, and terminal policy
    belong to the operation definition and ``OperationAuthority``. The plan
    layer owns only the semantic-surface shape, its retry declaration, and any
    terminal conditions the plan itself chose to declare.
    """

    errors: list[str] = []
    action = step.action
    label = f"step {step.step_id!r}"

    if is_controller_primitive(action):
        errors.append(
            f"{label} authors raw controller primitive {action.kind!r}; the generic "
            "surface accepts semantic actions only, because a bare coordinate "
            "carries no evidence about what it would activate"
        )
        return errors
    if step.success_conditions and not any(
        _is_causal_condition(condition.kind, condition.path)
        for condition in step.success_conditions
    ):
        errors.append(f"{label} declares success conditions but none witness a causal world change")
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

    Rebase owns plan chronology and the plan's own assumptions. Operation
    capability, selection, and reference eligibility are evaluated later by the
    one operation authority, first before scheduling and again in the input
    lease.
    """

    errors: list[str] = []
    if not plan.based_on_revision.same_snapshot_as(planner_observation.world_revision):
        errors.append("plan basis does not match its immutable planner snapshot")
    if not current_observation.world_revision.is_later_than(planner_observation.world_revision):
        errors.append("current world revision is not causally later than the planner snapshot")

    blocking = [
        event
        for event in current_observation.events
        if event in ("human_input_detected", "emergency_stop_detected")
    ]
    if blocking:
        errors.append(f"input authority was withdrawn during planning by {blocking[0]!r}")

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
        risk = risk_for_operation(step.action)
        if risk is None:
            continue
        attempts = 1 + step.retry_budget
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
    *,
    max_steps: int = LIVE_PLAN_MAX_STEPS,
) -> list[str]:
    """Every structural reason this authored plan cannot be scheduled.

    Returns an empty list when its own shape and declarations are coherent.
    Current operation eligibility is intentionally absent from this answer.
    """

    errors: list[str] = []

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
        errors.extend(_step_action_errors(step))

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
