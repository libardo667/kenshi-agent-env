---
schema_version: 1
subsystem: context_menu_orders
title: Derive target orders from Kenshi's muted context-menu path
proof_status: source_proven
executable:
  product: Kenshi
  version: 1.0.65
  architecture: x64
  steam_build_id: "13871665"
  sha256: a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1
libraries:
  - name: KenshiLib
    version: 0.4.0
    repository_commit: b566d74bf3d74629cc2fb632a97595b8202993f1
    artifact_sha256: d407bf18c807cd3390643227ca4dc3ee4628fedc520870eee250201c04db311d
    headers_sha256: fbfa33a283ed840e70f6f5f6675c2544df89bc18f8901b81eaa7095b466ec4c8
    working_tree_state: KenshiLib 0.4.0 checkout with locally modified library binaries; exact artifact and header hashes are authoritative
source_refs:
  - context_menu_build
  - context_menu_draw
  - selection_validity_predicate
  - task_probability_predicate
  - order_problem_side_effect
  - selected_character_dispatch
inferred_signature_confidence: medium
portable_test_refs:
  - portable_probe_semantics
live_probe_ids:
  - character_order_goal_adoption
crash_ids:
  - problem_check_world_load_crashes
contradiction_ids:
  - selection_filter_admits_every_task
  - odds_and_menu_disagree
  - problem_check_attempts_orders
remaining_uncertainty:
  - No exact retained run bundle A/B compares the muted probe with a player-visible right-click.
  - Historical live observations omit the executable and installed plugin hashes, command ids, and telemetry sequences.
  - Group delivery and world outcomes after task adoption remain unproven.
  - Executable inspection confirms code at each RVA but does not independently recover the inferred C++ signatures.
supersedes:
  - docs/reconstruction/interaction_proof_status.json perform_character_order reverse-engineering prose
  - native/KenshiAgentTelemetry/README.md context-menu reverse-engineering conclusion
---

# Conclusion

## Source-proven

For the exact binary and library fingerprints above, KenshiLib declares separate
menu-construction and menu-drawing entry points. Current native source calls the
saved `ContextMenu::showContextMenu` implementation under a guard, suppresses
`ContextMenuGUI::show`, copies the resulting `ContextMenu::orders`, and uses the
same combined probe again before dispatch. The candidate predicate signatures,
their RVAs, and the final task-dispatch entry point are recorded rather than
being implied by prose.

## Test-proven

Portable tests prove project-owned vocabulary mapping, evidence-source
preservation, menu-over-odds precedence, “not probed” handling, binding, and
wire validation. The compiled native fixture proves the same merge semantics.
Neither test class loads Kenshi, validates the ABI, or proves a world change.

## Live-proven

Historical sessions observed predicate disagreement, gameplay-facing side
effects, two crashes, target-specific offers, and later exact task adoption.
Those observations remain recorded in `dynamic_observations.json`, but no exact
run bundle preserves the required pre-state, request ids, acknowledgements,
later sequences, and binary hashes. They therefore inform the implementation
without supporting a durable `live_proven` repository classification.

## Withheld

Withhold claims that the silent probe is behaviorally identical to every real
right-click, that it is side-effect-free across all targets and game states,
that one accepted order reached every selected recipient, or that task adoption
proves the eventual world outcome. Re-establishing live proof requires a named
bundle with built and installed DLL hashes, pre-dispatch state, exact request,
acknowledgement, later engine evidence, and final disposition.
