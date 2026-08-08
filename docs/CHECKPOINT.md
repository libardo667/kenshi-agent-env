# Checkpoint: bounded closure before branch integration

This is the reproducible boundary after retiring the abandoned pointer/UI stack and
closing the unified inventory and resource-production proof loop. It records a dirty
pre-commit worktree intentionally; the integration commit has not been chosen yet.

## Repository

```text
base commit           a73e06c40a13f9052db6089018e1708c50ff03b6
branch                interaction-scope-order-lifecycle
tree state            DIRTY closure candidate at capture time
python (gate)         Python 3.12.13
native protocol       1.18.0
```

The worktree removes the retired camera, generic-screen, pointer-based inventory and
trade, legacy-harvest, and non-progress policy layers. The planner-visible gameplay
surface is now registry-backed and coordinate-independent. Host startup, recovery,
and narrow safety fallbacks still use synthesized Windows input where their contracts
require it.

## Portable gate

Run from the repository root:

```text
uv run pytest -q --color=no             passed (six expected strict xfails)
uv run ruff check .                      passed
uv run mypy src                          passed (145 source files)
uv run python scripts/export_schemas.py  checked-in schemas regenerated
uv run python scripts/export_docs.py     checked-in generated docs regenerated
git diff --check                         passed
```

The six strict xfails remain explicit reconstruction targets. They are not treated as
evidence for behavior that the current architecture has not demonstrated.

## Native artifact

Built in Release mode, exercised by `NativeCommandProtocolTests.exe`, installed only
while Kenshi was confirmed stopped, and rechecked with
`scripts/check_native_provenance.py`:

```text
declared protocol     1.18.0
source sha256         e1be701af23f4002be81b663791c83753b20122eb3127ad9a0fa7763a0758e2f
built sha256          048f1726c068da362c0fd3601387b6ed8858f65216f1e15c0cbe92d685df73bc
installed sha256      048f1726c068da362c0fd3601387b6ed8858f65216f1e15c0cbe92d685df73bc
declared capabilities 43
chain consistent      YES
```

The native fixture suite reported `Native protocol fixtures and semantics passed.`
The hash equality proves the installed file is the built file; the embedded protocol
and capability strings prove that build carried this source contract.

## Live closure evidence

All runs used the supervised disposable Kenshi fixture and ended with a confirmed
paused world. The run directories are local diagnostic artifacts under `runs/`.

- `closure-loot-20260808`: opened Fish and unconscious Burn as a paired inventory,
  then moved Burn's equipped `Katana` and `Halfpants (ragged)` into Fish. Both
  transfers reached the exact `item_transferred` terminal. Zero plan actions were
  rejected or aborted.
- `close_trade_window` recovery command
  `cmd-bec439c9589c4a4fb4fc72a06b561722`: closed the resulting looting window at
  telemetry sequence 1660 with terminal reason `trade_window_closed`. This is native
  recovery proof, not a claim that closing arbitrary windows is a planner operation.
- `closure-harvest-20260808`: exposed the cleanup omission that the surviving
  `produce_resource_output` operation had no affordance offer and resource owners were
  absent from trade-window offers. Its task-start acknowledgement is not counted as a
  completed harvest.
- `closure-harvest-fixed-20260808`: selected the repaired
  `produce_resource_output` affordance, adopted the exact already-running two-person
  resource task without reissuing it, stayed monitored until
  `resource_output_ready`, paired Fish with the Iron Resource, transferred one
  `Raw Iron`, verified the move, and stopped voluntarily. Zero plan actions were
  rejected or aborted.
- Recovery command `cmd-e5d5b8b05f354f768f4b14866c2c2d79` closed the final resource
  window with `trade_window_closed`.

The durable proof classification is in
`docs/reconstruction/interaction_proof_status.json`. In particular, one resource run
does not prove every current-selection or order-lifecycle case.

## Remaining boundary

- The generated interface audit deliberately reports the human UI affordances that
  are not planner-visible. Those gaps are not papered over with the deleted pointer
  handlers.
- `close_trade_window` is intentionally recovery-only. A planner-visible general
  close-window operation would require its own registry contract and evidence.
- The operation proof ledger still marks several navigation, threat-response, and
  group-recipient semantics unproven. This closure pass does not broaden those claims.
- Branch integration and GitHub publication are separate steps: inspect the final
  diff, commit this bounded slice intentionally, then merge it into `main` and push.
