---
schema_version: 1
subsystem: task_channels
title: Export ordinary orders, Jobs, permanent Jobs, and current activity independently
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
source_refs: [task_channel_owners, ordinary_order_samples, jobs_enabled, current_activity, task_evidence, jobs_ui]
inferred_signature_confidence: medium
portable_test_refs: [task_channels_portable_contract]
live_probe_ids: [task_channels_live_retained_order_and_activity]
crash_ids: []
contradiction_ids: [task_name_ownership_conflation]
remaining_uncertainty:
  - Exact controller-command ownership correlation remains unavailable in the singular native command model.
  - Ordinary queues larger than one have unknown totals and may omit middle entries.
  - No invalid or targetless live task subject was observed.
supersedes:
  - CharacterState.task_state and CharacterTaskState
  - orders plus orders_count and orders_complete
  - permajobs plus permajobs_count and permajobs_complete
  - task-name matching across Jobs, permanent Jobs, activity, and ordinary orders as controller ownership evidence
---

# Conclusion

## Source-proven

KenshiLib exposes separate ordinary-order, Jobs, permanent-Jobs, Jobs-enabled,
and current-goal owners. Tasker exposes task identity, subject, and description.
ForgottenGUI supports configured Jobs as indexed, enabled, movable UI entries but
does not own ordinary orders or current activity. ActionDeque exposes no total,
so a multi-entry ordinary queue's total and sampled tail position are unknown.

## Test-proven

The strict 1.20 model and shared native fixture represent a retained ordinary
order alongside current activity with empty Jobs. They reject the superseded
task-state/count shape, reject contradictory completeness, preserve null target
and position facts, and prevent Jobs/activity from proving a controller order.

## Live-proven

`task-channels-20260809T172100Z` uses matching built and installed DLL SHA-256
`3746e1dfd1feb9564c4a539388790b7d3f7f19e3971d965c08b223e8766b0d2f`.
Pre-dispatch state had empty ordinary orders, Jobs, and permanent Jobs, and null
activity. Request `cmd-536652d87c7b42e59187dc55c2278963` received an
acknowledgement at sequence 280; the conclusion instead comes from later engine
snapshot 329. Paste then had one retained ordinary `OPERATE_MACHINERY` order at
position 0 and separate `OPERATE_MACHINERY` current activity with null position,
while Jobs and permanent Jobs remained complete and empty. Final sequence 383
was loaded, paused, modal-free, and had no active native command.

## Withheld

Exact command-to-task ownership is withheld until causal native command records
can link recipients and task evidence. A sampled ordinary task is reported as
observed and unattributed, never adopted because its type or target looks similar.
No exact total or numeric position is claimed for the unenumerable middle/tail of
a multi-entry ordinary queue. No live unknown-subject case was observed, so null
target preservation remains source- and test-proven rather than live-proven.
