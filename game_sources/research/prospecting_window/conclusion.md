---
schema_version: 1
subsystem: prospecting_window
title: Read prospecting from Kenshi's populated window, not its terrain field
proof_status: live_proven
executable: {product: Kenshi, version: 1.0.65, architecture: x64, steam_build_id: "13871665", sha256: a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1}
libraries:
  - {name: KenshiLib, version: 0.4.0, repository_commit: b566d74bf3d74629cc2fb632a97595b8202993f1, artifact_sha256: d407bf18c807cd3390643227ca4dc3ee4628fedc520870eee250201c04db311d, headers_sha256: fbfa33a283ed840e70f6f5f6675c2544df89bc18f8901b81eaa7095b466ec4c8, working_tree_state: "KenshiLib 0.4.0 checkout with locally modified library binaries; exact artifact and header hashes are authoritative"}
source_refs: [unsafe_resource_field, prospecting_singleton, prospecting_show, prospecting_resource_lines]
inferred_signature_confidence: medium
portable_test_refs: [prospecting_portable_shape]
live_probe_ids: [prospecting_dialogue_lifecycle_20260810]
crash_ids: [null_biome_resource_crash]
contradiction_ids: [zero_does_not_deny_deposit]
remaining_uncertainty:
  - The displayed scalar's aggregation, area, and unit semantics are unknown.
  - The window member layout is not independently recovered from the executable.
  - The displayed values were observed verbatim; their physical units and aggregation remain unknown.
supersedes:
  - docs/reconstruction/interaction_proof_status.json survey_local_resources reverse-engineering prose
  - native/KenshiAgentTelemetry/README.md prospecting reverse-engineering conclusion
---

# Conclusion

## Source-proven

Current source reads the populated Prospecting window through the exact
KenshiLib declarations above and no longer calls the terrain field directly.

## Test-proven

Portable and compiled fixtures prove the project-owned serialized survey shape,
not the window ABI or live caption values.

## Live-proven

The exact r7 bundle proves the timed request-to-populated-caption chain, native
window close, restored pause, later exact Barman dialogue, and final clean
world state against hash-bound executable and DLL artifacts. The committed
reduced evidence retains the decisive frames and hashes the omitted raw run.

## Withheld

Withhold interpretations of the scalar's unit, area, aggregation, or discrete
deposit meaning. This bounded lifecycle proof is not a long-duration stability
claim and does not cover every location or science-skill value.
