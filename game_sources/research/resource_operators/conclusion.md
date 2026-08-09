---
schema_version: 1
subsystem: resource_operators
title: Export exact resource capacity, accepted operators, and output inventory
proof_status: live_proven
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
source_refs: [resource_operator_storage, resource_slot_admission, resource_workers_ui, resource_output_inventory, assigned_work_channels, ambiguous_work_progress]
inferred_signature_confidence: medium
portable_test_refs: [resource_operator_portable_contract]
live_probe_ids: [resource_operator_capacity_one_live]
crash_ids: []
contradiction_ids: [selected_worker_conflation]
remaining_uncertainty:
  - Resource-specific assigned workers are unavailable when character task subjects are null.
  - No semantically reliable natural-resource work-progress scalar has been established.
  - Operator identities are session-scoped; no cross-load continuity is claimed.
  - A nonempty output stack was not observed in this bounded live run.
supersedes:
  - selected recipient count as resource operator count or capacity
  - OPERATE_MACHINERY goal adoption as resource operator acceptance
  - animation as evidence of resource operator acceptance
---

# Conclusion

## Source-proven

Kenshi exposes exact capacity in `UseableStuff::numOperatorsMax` and exact
accepted membership in `UseableStuff::currentOperators`. Admission disassembly
compares the set size with capacity before insertion; the worker GUI walks the
same state. Output stacks are exactly enumerable from inventory section `out`.

Assigned or queued work remains character-owned evidence and may have an
unresolved subject. It is not accepted membership. The available progress-like
floats lack a proven natural-resource semantic, range, and rollover contract, so
no public work-progress value is exported.

## Test-proven

Protocol 1.21 exports nullable `operator_capacity`, exact
`current_operator_ids`, explicit completeness flags, and exact output stacks.
Strict models reject duplicate and over-capacity identities. Planner binding and
affordance generation withhold resource operations without the new state
capability or complete required state. A two-character selection with capacity
one still reports only the one identity Kenshi accepted.

## Live-proven

`resource-operators-20260809T201826Z` uses matching built and installed DLL
SHA-256 `91526b828e44035b0cb6de5a22b7cc5ad0c2e392b66a7b8adcbf9ae9403d8db8`.
Before dispatch, two exact identities were selected while the exact
capacity-one target's accepted set was complete and empty. After the exact
two-recipient request, both identities had ordinary work queued against that
target, but `current_operator_ids` contained only Ribs. The native terminal
waited for that set transition and completed as `resource_operator_accepted`.
Final sequence 878 was loaded, paused, modal-free, and had no active command.

## Withheld

No resource-specific assigned-worker identity list is fabricated from character
queues when task subjects are unresolved. No selected character is called an
operator until their exact identity appears in `currentOperators`. No operator
capacity is estimated from selection size or animation. No work-progress scalar
is published from `progressBarLevel`, `getOutput`, or GUI presentation state.
