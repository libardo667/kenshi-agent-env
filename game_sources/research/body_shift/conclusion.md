---
schema_version: 1
subsystem: body_shift
title: Enter one exact eligible body through Kenshi's faction and selection model
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
source_refs: [recruit_character, create_squad, change_character_faction, repair_selection_handle, select_entered_body]
inferred_signature_confidence: medium
portable_test_refs: [body_shift_portable_contract]
live_probe_ids: [molly_manual_shift]
crash_ids: []
contradiction_ids: []
remaining_uncertainty:
  - The manual Molly observation lacks a named run bundle and exact native provenance.
  - Repetition, failure rollback, and every eligibility boundary remain unproven live.
  - Executable inspection does not independently recover the inferred signatures.
supersedes:
  - docs/reconstruction/interaction_proof_status.json shift_into_body reverse-engineering prose
  - native/KenshiAgentTelemetry/README.md body-shift reverse-engineering conclusion
---

# Conclusion

## Source-proven

The exact KenshiLib declarations and current native call sites support an
elective exact-body transition through recruit, squad creation, faction move,
selection-handle repair, and exclusive tracked selection.

## Test-proven

Portable and compiled fixtures prove authorability, binding, empty-selection
recovery shape, and wire conformance. They do not prove engine behavior.

## Live-proven

A supervised Molly dispatch observed later faction/selection consequences, not
merely a returned call. Because no exact run bundle and hash chain survive, the
repository does not classify the subsystem as durably live-proven.

## Withheld

Withhold broad claims about repeatability, rollback, all body types, and the
complete live transition until a named bundle records pre-state, request,
acknowledgement, later engine evidence, hashes, and final disposition.
