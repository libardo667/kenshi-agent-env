---
schema_version: 1
subsystem: player_topology
title: Export distinct player roster, platoon, active, primary, and selection topology
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
source_refs: [player_roster, character_platoon, platoon_string_id, platoon_name, active_platoon, primary_character, selected_character_set]
inferred_signature_confidence: medium
portable_test_refs: [player_topology_portable_contract]
live_probe_ids: [player_topology_multiplatoon_live]
crash_ids: []
contradiction_ids: [character_handle_changed_on_platoon_move]
remaining_uncertainty:
  - Character entity IDs are session-scoped; no cross-session character-ID continuity is claimed.
  - Empty management rows have no player-character linkage and are not exported as platoons.
  - Longer-run roster mutation and pointer-lifecycle coverage remains follow-on work.
  - Executable inspection does not independently recover the inferred signatures.
supersedes:
  - TelemetrySnapshot.squad and CharacterState.selected
  - UIState.selected_character_id and UIState.selected_character_ids
---

# Conclusion

## Source-proven

KenshiLib and current native call sites expose separate owners for the complete
player roster, character-to-platoon linkage, stable candidate platoon string
identity, platoon name, active platoon, primary character, and selected set.
ForgottenGUI was inspected and is not used as the topology owner.

## Test-proven

The strict Python model and compiled native conformance fixture preserve two
platoons and exact membership, distinguish active from primary and selection,
prove primary is not derived from roster order, and reject the superseded
`squad`, per-character `selected`, and UI-owned selection shape.

## Live-proven

`player-topology-20260809T161112Z` records matching built and installed DLL
SHA-256 `2dfee3ca27a3a2494b31386cff06e9db2ad02e38e7d3d6079fec0fb2234436bc`,
every pre-state, request, acknowledgement where one exists, later raw
frame-plus-telemetry evidence, the exact changed quicksave files, and a paused
final disposition.

The bundle proves two nonempty player platoons and exact linkage, Tab changing
the active platoon independently of primary and selection, exact primary and
complete-selection changes, and Paste retaining one full session-scoped ID
while moved between platoons and back. F9 advanced the GameWorld identity
session and restored both platoon string IDs, names, memberships, primary
Paste, and selected `[Paste]`. Kenshi reset the active tab to `Nameless_0`
rather than persisting saved `Nameless_1`; that observed distinction is part of
the proof.

## Withheld

Character IDs are opaque and stable only inside one identity session. No claim
is made that the same character ID crosses load, that empty UI-created squad
rows exist in the player/platoon structures, or that this bounded run proves
arbitrary future roster churn. Those longer lifecycle questions are named
follow-on work, not fallback authority in the current producer.
