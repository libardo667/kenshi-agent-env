---
schema_version: 1
subsystem: dialogue_options
title: Model every current dialogue reply as an exact native affordance
proof_status: live_proven
executable: {product: Kenshi, version: 1.0.65, architecture: x64, steam_build_id: "13871665", sha256: a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1}
libraries:
  - {name: KenshiLib, version: 0.4.0, repository_commit: b566d74bf3d74629cc2fb632a97595b8202993f1, artifact_sha256: d407bf18c807cd3390643227ca4dc3ee4628fedc520870eee250201c04db311d, headers_sha256: fbfa33a283ed840e70f6f5f6675c2544df89bc18f8901b81eaa7095b466ec4c8, working_tree_state: "KenshiLib 0.4.0 checkout with locally modified library binaries; exact artifact and header hashes are authoritative"}
source_refs: [dialogue_reply_rows, dialogue_reply_clicked]
inferred_signature_confidence: medium
portable_test_refs: [dialogue_option_portable_contract]
live_probe_ids: [mercenary_hiring_dialogue_20260810]
crash_ids: []
contradiction_ids: []
remaining_uncertainty:
  - DialogueWindow member offsets are header-derived rather than independently recovered.
  - The live proof establishes exact selection, payment, and closure, not later mercenary AI behavior.
supersedes:
  - planner behavior that could only close and reopen a dialogue without selecting a reply
---

# Conclusion

## Source-proven

Current source copies the complete visible ordered reply list and calls the
public `Dialogue::replyClicked(int)` only after exact target/index/caption
revalidation. It retains no engine pointer across frames.

## Test-proven

Strict Protocol 1.6 fixtures, generated schemas, operation-registry checks,
mock/replay conformance, and affordance tests prove the project-owned plural
surface and fail-closed rebinding behavior.

## Live-proven

The exact Mercenary Captain bundle proves two zero-primitive native selections:
the first changed the complete option list to explicit prices; the second paid
c.2,000 and closed the dialogue. The committed reduced evidence retains the
decisive request, acknowledgements, telemetry deltas, rendered frames, binary
hashes, omitted-run hashes, and final disposition.

## Withheld

Do not infer contract duration, bodyguard membership, or later mercenary AI
conduct from payment and dialogue closure. This bounded proof is not the
100-plus-turn survival soak.
