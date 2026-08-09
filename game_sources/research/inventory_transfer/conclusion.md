---
schema_version: 1
subsystem: inventory_transfer
title: Move exact items through Kenshi's inventory model with explicitly simplified shop pricing
proof_status: live_proven
executable: {product: Kenshi, version: 1.0.65, architecture: x64, steam_build_id: "13871665", sha256: a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1}
libraries:
  - {name: KenshiLib, version: 0.4.0, repository_commit: b566d74bf3d74629cc2fb632a97595b8202993f1, artifact_sha256: d407bf18c807cd3390643227ca4dc3ee4628fedc520870eee250201c04db311d, headers_sha256: fbfa33a283ed840e70f6f5f6675c2544df89bc18f8901b81eaa7095b466ec4c8, working_tree_state: "KenshiLib 0.4.0 checkout with locally modified library binaries; exact artifact and header hashes are authoritative"}
source_refs: [open_trade_pair, open_inventory_map, resolve_inventory_slot, remove_inventory_item, add_inventory_item, detect_shop_trade, price_item, move_inventory_money]
inferred_signature_confidence: medium
portable_test_refs: [inventory_portable_contract]
live_probe_ids: [equipped_body_loot, resource_output_transfer]
crash_ids: [autotrade_three_crashes]
contradiction_ids: [gui_autotrade_abi_mismatch, partial_add_false_return]
remaining_uncertainty:
  - Named movement bundles omit built and installed DLL hashes.
  - Shop pricing is project-owned and does not reproduce Kenshi adjudication.
  - The RClickAutoTrade ABI remains unresolved and intentionally unused.
  - Owner kinds, stack boundaries, rollback paths, and equipment sections beyond the named probes remain unproven.
supersedes:
  - docs/reconstruction/interaction_proof_status.json open_trade_window and transfer_item reverse-engineering prose
  - native/KenshiAgentTelemetry/README.md inventory ABI and transfer conclusion
---

# Conclusion

## Source-proven

Current source opens exact paired windows and moves one addressed item through
the inventory model calls pinned above. It contains no `RClickAutoTrade` call
or fallback. Shop detection and simplified pricing are explicit project logic.

## Test-proven

Portable and compiled fixtures prove request shape, exact addressing, offers,
acknowledgement parsing, and the absence of the retired GUI call. They do not
load Kenshi.

## Live-proven

The named loot and resource-output bundles contain pre-state, exact command ids,
completed acknowledgements, and later inventory loss/gain. This proves only the
named model-level movements. The bundles omit DLL hashes, which remains a
provenance limitation rather than grounds to generalize further.

## Withheld

Withhold claims about Kenshi trade adjudication, theft, haggling, faction and
stolen-goods rules, the unresolved GUI ABI, and untested owner or stack cases.
