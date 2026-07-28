"""Authority decisions must remain visible to the mutation campaign."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "kenshi_agent"

# Existing decorated decisions are admitted only until their owning slice changes
# them. Every exact entry carries the migration reason so this cannot silently
# bless a newly decorated decision.
_PREEXISTING_DECORATED_DECISIONS = (
    "advisor:GuideCorpus.references_are_unique_and_resolved",
    "approach:ApproachStatus.is_terminal",
    "approach:ApproachStatus.should_abort",
    "authored_starts:AuthoredStartsManifest.identities_are_unique",
    "config:AppConfig.planning_risk_matches_control_mode",
    "config:ControlsConfig.all_speeds_present",
    "config:ControlsConfig.calibrated_client_size_is_complete",
    "config:ControlsConfig.startup_control_labels_are_nonempty",
    "config:LaunchConfig.required_profile_has_a_file",
    "config:MacroConfig.valid_movement_pulse_bounds",
    "config:NormalizedPointerBoundsConfig.ordered_bounds",
    "config:PlannerConfig.output_token_ceiling_covers_base",
    "continuity:ContinuityLedger.recent_action_outcomes",
    "continuity:ContinuityLedger.recent_plan_outcomes",
    "continuous_executor:_StagedPatch.interrupts_active_step",
    "control/base:WindowRect.height",
    "control/base:WindowRect.width",
    "control/win32:Win32InputController._input_lease",
    "display_lease:external_display_lease",
    "graphics_profile:GraphicsApplyResult.backup_path",
    "graphics_profile:RendererProfile.section_is_plain",
    "memory:MemoryStore.schema_version",
    "models:AffordanceRequestRecord.key_matches_action",
    "models:AppendFieldbookEntryOperation.normalize_nonblank_content",
    "models:CapabilityCondition.validate_capability_path",
    "models:Condition.normalize_unambiguous_model_noise",
    "models:CreateFieldbookProjectOperation.normalize_nonblank_text",
    "models:FieldCondition.validate_target_shape",
    "models:FieldbookReadReceipt.result_ids_and_status_match",
    "models:InventoryItem.name_falls_back_to_item_name",
    "models:MemoryReadReceipt.result_ids_match_returned_records",
    "models:NativeCommandAcknowledgement.validate_causal_lifecycle",
    "models:NativeCommandRequest.validate_native_fences",
    "models:NativeControlState.acknowledgement_ids_are_unique",
    "models:NormalizedPointerBounds.validate_order",
    "models:PlanEnvelope.validate_graph_and_action_bound",
    "models:PlanStep.retry_requires_idempotency",
    "models:ReadFieldbookAction.has_a_read_selector",
    "models:ReadFieldbookAction.normalize_query",
    "models:RecallSummary.complete",
    "models:ScrollAction.notches_must_move",
    "models:ScrollScreenAction.notches_must_move",
    "models:SkillAction.accept_argument_mapping",
    "models:TelemetrySnapshot.captured_at_must_be_aware",
    "models:TelemetrySnapshot.stable_identity_must_be_complete_and_consistent",
    "models:UpdateFieldbookSummaryOperation.normalize_nonblank_summary",
    "models:VisibleUIControl.center",
    "models:VisibleUIControl.truncate_long_label",
    "models:_ConditionBase.validate_common_shape",
    "mutation_campaign:_batch_workspace_lock",
    "options:StatefulNativeMovementOption._required_capability",
    "options:StatefulNativeMovementOption._wire_command",
    "overlay:WindowRect.height",
    "overlay:WindowRect.width",
    "overlay:_dock_beside_windows_terminal.collect_terminal",
    "safety_supervisor:SafetySupervisor.preempted",
    "ui_affordances:Affordance.covered",
    "ui_affordances:Affordance.strands_the_agent",
    "ui_affordances:AffordanceReport.pixel_dependencies",
    "world_state:ObservationPump.running",
    "world_state:WorldStateStore.active_plan",
    "world_state:WorldStateStore.latest",
)
DECORATED_DECISION_EXEMPTIONS: dict[str, str] = {
    decision: "Pre-existing decorated decision; migrate in its owning slice."
    for decision in _PREEXISTING_DECORATED_DECISIONS
}

DECISION_NODES = (ast.If, ast.Compare, ast.BoolOp, ast.BinOp, ast.IfExp)


def _mutmut_skips(decorators: list[ast.expr]) -> bool:
    canonical = [
        decorator
        for decorator in decorators
        if not (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "_mutmut_mutated"
        )
    ]
    if len(canonical) != 1:
        return bool(canonical)
    decorator = canonical[0]
    return not (
        isinstance(decorator, ast.Name)
        and decorator.id in {"staticmethod", "classmethod"}
    )


def _decorated_decisions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    decisions: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            if _mutmut_skips(node.decorator_list) and any(
                isinstance(descendant, DECISION_NODES)
                for statement in node.body
                for descendant in ast.walk(statement)
            ):
                relative = path.relative_to(SOURCE_ROOT).with_suffix("")
                qualified = ".".join((*self.scope, node.name))
                decisions.add(f"{relative.as_posix()}:{qualified}")
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

    Visitor().visit(tree)
    return decisions


def test_decorated_authority_decisions_have_explicit_migration_exemptions() -> None:
    found = {
        decision
        for path in SOURCE_ROOT.rglob("*.py")
        for decision in _decorated_decisions(path)
    }
    unexplained = {
        decision
        for decision, reason in DECORATED_DECISION_EXEMPTIONS.items()
        if not reason.strip()
    }
    missing = sorted(found - set(DECORATED_DECISION_EXEMPTIONS))
    stale = sorted(set(DECORATED_DECISION_EXEMPTIONS) - found)

    assert not unexplained
    assert not missing and not stale, (
        "Missing exemptions:\n"
        + "\n".join(missing)
        + "\nStale exemptions:\n"
        + "\n".join(stale)
    )
