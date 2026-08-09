# Checkpoint: truthful Kenshi resource operators

This checkpoint records the coherent Protocol 1.21 resource-operator slice.
Source-proven, test-proven, live-proven, and withheld conclusions remain
separate.

## Repository and authority

```text
parent commit          20beff9499311f12adc545fd5d1db16951c02afc
integration branch     main
starting remote        origin/main at 5753bcb8ca993d46964fc35349338e1f91855bce
starting tree          clean
producer protocol      1.21.0
request schema         1.4
loaded capabilities    49
```

Current `main` was remeasured after the preceding agent committed its work.
`20beff9` owns independent task channels, `89457e0` owns player topology, and
`5753bcb` owns the accepted Protocol 2.0 world-model decision. The current
resource authority is `WorldTarget` in `src/kenshi_agent/core/telemetry.py`,
native collection and monitoring in
`native/KenshiAgentTelemetry/KenshiAgentTelemetry.cpp`, the capability
manifest, generated schemas, and
`game_sources/research/resource_operators`.

## Coherent 1.21 slice

Every natural-resource target now carries:

- nullable exact `operator_capacity` from `UseableStuff::numOperatorsMax`;
- `current_operator_ids` from the full engine `currentOperators` set plus an
  explicit completeness flag;
- exact current output stacks from inventory section `out` plus an explicit
  completeness flag; and
- no fabricated work-progress scalar.

The planner-visible `world.resource_operators` capability exists only with this
contract. Resource affordances and bindings require the capability and complete
operator state; bounded production additionally requires complete output
inventory. Descriptions state the exact capacity and accepted identities and
say directly that selection and queued work are not acceptance.

Native `perform_context_action` no longer completes resource operation when the
primary character merely adopts an `OPERATE_MACHINERY` goal. It completes as
`resource_operator_accepted` only when an exact selected identity appears in
the target's engine-owned accepted set. Bounded production uses the same set for
adoption and progress and releases controller-issued work across all recorded
recipients rather than only the primary.

## Source-proven

- KenshiLib 0.4.0 `UseableStuff.h` declares `numOperatorsMax` at offset
  `0x3AC`, `currentOperators` at `0x3D0`, and separate `isFreeSlot`,
  `tryOperate`, `stopOperating`, and `getGUIWorkers` methods.
- Current Kenshi executable admission bodies around RVAs `0xF7BF0` and
  `0xF8030` compare the set size at `0x3E0` with capacity at `0x3AC`; the
  latter inserts the handle into the set only after admission. The worker GUI
  body around `0x3075B0` reads the same capacity and set.
- `Inventory::getSection("out")`, `InventorySection::getItems`, and valid
  `Item` name, quantity, and type expose exact current output stacks.
- Character ordinary orders, Jobs, permanent Jobs, and activity are separate
  assigned/queued-work evidence. A task subject may be null, and no separate
  resource-owned assigned-worker collection was found.
- `progressBarLevel`, `getOutput`, `productionState`, and GUI output-progress
  calls exist, but their declarations do not establish a natural-resource
  meaning, range, monotonicity, or rollover contract.

Exact header, library, include-tree, executable hashes, address drift,
declarations, disassembly conclusions, and native call sites are recorded in
`game_sources/research/resource_operators`.

## Test-proven

- Strict model tests reject duplicate accepted identities, complete identity
  state without capacity, accepted sets larger than capacity, and resource
  fields on non-resource targets.
- Affordance tests pin a two-character selection with capacity one and only one
  accepted identity, and withhold both resource operations when engine state is
  incomplete.
- Binding tests withhold production without the new capability, complete
  accepted identities, or complete output inventory.
- The planner digest carries exact resource capacity, accepted identities,
  output stacks, and completeness flags.
- Shared C++ fixtures serialize exact capacity, accepted identities, and output
  stacks. The VS2010 SP1 x64 conformance executable reported
  `Native protocol fixtures and semantics passed.`

## Native build and installed provenance

Kenshi was safely paused and closed before installation. Windows process
absence was confirmed before the old DLL was replaced, then the new DLL was
loaded by a fresh authored-start launch.

```text
built DLL path          C:\Users\levib\AppData\Local\KenshiAgent\build\native\bin\KenshiAgentTelemetry.dll
built DLL sha256        91526b828e44035b0cb6de5a22b7cc5ad0c2e392b66a7b8adcbf9ae9403d8db8
built DLL size          424448 bytes
installed DLL path      C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll
installed DLL sha256    91526b828e44035b0cb6de5a22b7cc5ad0c2e392b66a7b8adcbf9ae9403d8db8
installed DLL size      424448 bytes
conformance exe sha256  c57f19c8de54a083cdd9df950685d12df146e4b942fd3c0114e0dfd74c170e6f
conformance exe size    292352 bytes
built equals installed  YES
provenance chain        fresh 1.21 process; 49 capabilities; world.resource_operators present
```

## Live-proven

Exact bundle: `runs/resource-operators-20260809T201826Z/manifest.json`.

- Fresh paused sequence 649 selected exact Ribs and Hand. The exact Small Iron
  Resource reported capacity 1, a complete empty accepted set, and complete
  empty output inventory. Both characters still had work against a different
  resource, which did not make them operators of this target.
- Request `cmd-06957502b99144479744d93740f998fd`, authored from fresh sequence
  749, selected exact Ribs and Hand and the exact capacity-one target.
- Sequence 750 acknowledged only `accepted/issued`; it was not treated as
  gameplay success.
- Engine sequence 803 completed as `resource_operator_accepted` after exact
  selected identity Ribs entered the target set.
- Later raw sequence 831 still selected both identities and showed an exact
  ordinary `OPERATE_MACHINERY` order against this target for both. Capacity
  remained 1 and the complete accepted set contained only Ribs. Ribs had
  matching current activity; Hand had null activity.
- After recovery, final sequence 878 was loaded, paused, modal-free, and had no
  active native command. Two selected and two queued identities remained
  visibly distinct from one accepted operator.

This is the decisive product boundary: **selected two, queued two, accepted
one**. Neither selection nor queued work can be reported as operator identity.

## Withheld and named follow-on work

- **Work progress:** no percentage or scalar is exported from
  `progressBarLevel`, `getOutput`, `productionState`, or GUI state until its
  natural-resource semantic, range, and rollover behavior are proven.
- **Resource-specific assigned workers:** character task subjects are exported
  when resolved. When they are null, no resource assignment is inferred from
  task name, selection, proximity, or animation.
- **Cross-session identity:** accepted operator IDs are exact only within one
  native identity session.
- **Nonempty output live sample:** exact output stacks are source-, fixture-,
  and prior inventory-transfer proven, but this bounded 1.21 operator run saw
  an empty output slot.
- **Revised bounded-production live terminal:** current 1.21 production
  monitoring is source- and test-proven against the exact set and output
  inventory. The older output bundle used the superseded task-adoption terminal,
  so a fresh output-ready bundle remains follow-on work.

## Verification

The complete portable gate passed over the final candidate after this checkpoint
and generated documentation settled:

```bash
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
```

It covers locked dependency sync, Ruff, strict mypy, research-package
validation, schema and generated-document freshness, the complete pytest suite,
and `git diff --check`.
