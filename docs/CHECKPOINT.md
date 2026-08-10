# Checkpoint: 120-turn native survival soak

This goal exercises the native semantic surface for 120 open-ended planning
turns and fixes failures exposed by the soak without weakening truthful
terminals or non-progress guards. The first two attempts exposed an incremental
production-contract bug and then an unnecessary exact-identity transcription
requirement after otherwise completing mining, travel, dialogue, food
purchases, and bodyguard hire.

## Repository and authority

```text
parent commit          72dddb637a4ff8c456eefb620cd8fd9186c4235a
integration branch     main
starting tree          clean
producer protocol      2.0.0
request schema         1.6
```

The current authorities are
`native/KenshiAgentTelemetry/KenshiAgentTelemetry.cpp`,
`native/KenshiAgentTelemetry/InventoryScreenSemantics.h`,
`src/kenshi_agent/core/telemetry.py`, and the operation definitions and live
handlers that consume those contracts. There is no reader for
`TelemetrySnapshot.squad`, `native_control`,
`NativeControlState.active_command_id`, or the former `acknowledgements` shape.

`controller_commands.commands` is now plural at the producer as well as every
consumer. The temporary one-record publisher carried an explicit deletion
deadline of **2026-09-20** and was removed early on **2026-08-10**. Native emits
the bounded retained registry directly. This lets a synchronous native clock
command remain visible while one gameplay command is monitored; it does not
claim simultaneous active gameplay-command ownership.

## Productive mining contract

- Natural-resource `operate` remains a routable low-level standing-job command,
  but it is not planner-authored. The resource offer exposes only
  `produce_resource_output`, whose native monitor owns exact accepted-operator
  admission, advances the complete observed output total by one up to five,
  waits for that exact threshold, and releases controller-owned work.
- The productive command establishes gear 3 / 5x through the native
  `set_speed` command. No speed key, pause key, pointer action, or input lease is
  used in a healthy native-assisted session.
- Native time control completes synchronously without replacing or mutating the
  active monitored gameplay command. Both records remain independently
  addressable in the plural retained-command collection.
- The generic `wait` affordance truthfully exposes the live execution ceiling
  of zero through eight real seconds. Longer productive intervals belong to
  the operation that can observe their progress and terminal, not to planner
  chains of speculative waits.

## Local trade contract

- `ForgottenGUI::showTradeWindow` can render a pair without enforcing distance.
  The controller therefore applies a conservative 30-unit three-dimensional
  authoring fence before asking it to draw anything. This is a controller
  safety fence, not a claim about Kenshi's private exact trade radius.
- Offer generation, operation binding, live input-boundary revalidation, and
  native game-thread dispatch all fail closed on unknown or excessive distance.
  The first owner must be the exact current primary and the two owners must be
  distinct.
- Once a local pair has been requested, native reports `accepted` /
  `trade_window_requested` while the GUI updates. The live handler waits for
  terminal `completed` / `trade_window_open`; the post-open engine-owned
  `InventoryGUI::isWithinRangeToTrade` result remains authoritative.
- A far but perceived vendor may still be approached. Mere visibility no
  longer creates an `open_trade_window` affordance.

## Current evidence lanes

Source- and focused-test-proven:

- the producer serializes every retained record up to its existing bound of 16
  and contains no `PublishedNativeCommandRecord` singleton bridge;
- clock dispatch is admitted while one gameplay command is active without
  mutating that command's ownership fields;
- productive mining selects 5x natively and emits zero desktop primitives;
- natural resources no longer advertise the indefinite generic operation;
- remote, same-owner, non-primary, stale, and unknown-distance trade requests
  fail before rendering;
- accepted trade waits for the exact later native terminal; and
- the wait affordance schema exposes the eight-second live ceiling.

Build- and installed-proven: the Release x64 build completed and its compiled
conformance executable reported `Native protocol fixtures and semantics
passed.` The built, preserved replacement, and installed DLL SHA-256 values
match exactly while Kenshi was closed.

Live-proven: `native-mining-local-trade-regression-20260810-r1` restored and
attested the exact fixture, completed in 12 turns, and used no generic resource
`operate` or planner `wait`. Productive command
`cmd-c1e8df5591a244e796b3f77b9f62b3e0` reached
`resource_output_ready_task_released` at sequence 211 with one Copper in the
resource. Sequences 158 and 159 retained that accepted command beside completed
native clock records; the action outcome reported speed 0.0 to 5.0.

Collection command `cmd-4341e3e8d6fa40e09124ef7bbbb3ce19` emptied the
resource and raised Slowline's inventory item count from two to three. Map
travel reached The Hub, then a separate physical approach reduced Barman
distance from 312.47 to 16.73 before any Barman trade request. That approach
conservatively ended `movement_stalled`; the later local state was still valid
for trade and the caveat is preserved rather than relabelled as movement
success.

Local trade command `cmd-482c905e973740d8b9bf6b88331b70ce` completed
`trade_window_open` at sequence 345. Sale command
`cmd-da8dc4d8084440618b71944a1456afbe` completed `item_transferred` at
sequence 362; later engine state removed Copper from Slowline, reduced the
inventory count to two, and increased money from c.20,000 to c.20,195. Every
one of the 12 action receipts recorded zero primitive actions. Final sequence
421 was paused, interface-clear, and work-clear; final cleanup reported
`input_attempted=false` and `input_executed=false`. The supported close then
reported `Kenshi closed from a fresh paused idle state.`

The committed reduced artifact
`docs/reconstruction/native_mining_local_trade_20260810.json` contains the run
manifest, fixture attestation, exact request/acknowledgement chain, decisive
telemetry, visual frame descriptions, DLL/PDB identities, hashes for the
omitted 12.8-MiB event stream and all raw PNGs, and final disposition.

## Soak findings and candidate repairs

Run `protocol-2-native-survival-soak-20260810-r5` ended safely after 18
completed plans rather than reaching the 120-turn ceiling. Its last three
structured plans each stated an objective to travel to the Copper Resource,
but selected the exact `observe` / `noop` affordance. These were not parser,
transport, or fallback failures: structured output was accepted and the
planner chose the no-op itself. The unchanged-state guard then stopped the run
after the third completed no-op, which is the intended safety behavior.

At that state the Copper Resource was 1,850 units away and its complete output
inventory already contained one Copper. `produce_resource_output` was still
offered, but its bound action retained the default absolute threshold of one
and its planner-visible description promised only to work until any stock
existed. Selecting it therefore could not travel, work, or change state.

The candidate repair derives each offer from complete engine output state. An
observed total of one now binds `minimum_output_quantity=2` and explicitly
offers to path to the resource and produce one additional item. The threshold
advances up to the native action maximum of five; a full output withholds
production until collection makes space. Focused affordance, binding, model,
and operation-definition tests pass. The complete portable gate also passes;
the next run proved both native Iron thresholds one and two reached their exact
terminals rather than returning immediate success.

Run `protocol-2-native-survival-soak-20260810-r6` then ended safely after 13
turns. After bodyguard hire, three planner responses selected the sole current
`produce_resource_output` offer but each omitted the same middle segment from
its 100-character Copper Resource identity. Structured output was rejected
before execution every time; no guessed identity or input crossed the boundary.

`target_id` was already optional in the semantic selection model, but the
planner instructions always required copying it. The candidate planner digest
now marks whether an exact target is actually needed for disambiguation. A
semantic with exactly one current offer exposes `target_id_required=false` and
`target_id=null`; resolution binds that unique semantic to the full
runtime-owned engine identity. Multiple offers with the same semantic still
expose and require their exact IDs, and a missing or incorrect disambiguator
still fails closed. Focused planner, budget, proposal, and affordance tests
pass, as does the complete portable gate. The 120-turn rerun remains pending.

Run `protocol-2-native-survival-soak-20260810-r7` reached 34 turns and proved
incremental Iron output thresholds one through five, Copper production,
travel, food and medical purchases, and the complete Ruka recruitment
dialogue. Kenshi then opened its mandatory `IMPORT CHARACTER` editor while the
ordinary roster and selection were empty. The model had no semantic operation
for that surface, so the unchanged-state guard safely stopped after three
observe-only plans. The user completed recruitment and saved
`kenshi-ruka-recruit`; it is captured immutably as scenario
`ruka-recruited-survival-soak-20260810` with fixture digest
`96d3a749a5a85bb9e44b064e2516da25c3faeb45812364ccfe11637c6ec9c917`.

The candidate now exports `ui.character_editor_open`, reports
`active_screen=character_editor`, withholds generic interface closure, and
offers only `accept_recruited_character`. Its native command resolves the exact
layout-owned `ConfirmButton`, invokes the game-owned click event without a
mouse, cursor, key, or coordinate, and remains accepted until a later engine
frame proves character-editor mode absent. Source, strict models, shared wire
fixture, portable tests, compiled conformance, and installed parity are proven;
the new command's live outcome remains explicitly unproven.

## DLL artifacts and exact commands

Both sides of the installation are preserved:

```text
pre-change DLL path     C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-character-editor-confirmation\pre-change\KenshiAgentTelemetry.dll
pre-change DLL sha256   76310e286fc4caf833ee865e0c68c01c25f4382cbbbc39db92e5640050b08f62
pre-change DLL size     436736
replacement DLL path    C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-character-editor-confirmation\replacement\KenshiAgentTelemetry.dll
replacement DLL sha256  7aae1d50a36f8fdd6ddfbf43392632ebfd597b3306ef3c9c0af14b53fc5a6b42
replacement DLL size    440320
replacement PDB sha256  0e601c7e68623b15d902018f1aacf7270edd280212a32df2899b2deed652ee95
replacement PDB size    11136000
conformance exe sha256  46411ba3aa44eb0802027a185cc5af460005ddfe7b7749922096e4f2fd946af6
conformance exe size    322560
installed parity        YES
```

Rollback exactly to the pre-change DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-character-editor-confirmation\pre-change\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Build the candidate:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build_native.ps1
```

Reinstall the exact candidate replacement DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-character-editor-confirmation\replacement\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

## Completion boundary

The named targeted live regression proves:

1. exact productive mining creates output under native 5x playback;
2. the output is collected into the selected character's inventory;
3. the character travels to a local vendor interaction distance;
4. the Barman trade request was not dispatched until after physical approach;
   source and tests separately prove remote offers and pre-render dispatch fail
   closed;
5. local trade reaches `completed` / `trade_window_open` and transfers the
   mined item; and
6. no mouse, pointer, keyboard, false acknowledgement, or planner wait loop was
   used.

The current goal is complete only after a fresh run reaches all 120 turns, its
bundle is audited for crashes, early stops, rejected or aborted actions,
primitive desktop input, and final safe state, and Kenshi is closed cleanly.

## Verification

The complete portable gate passed on 2026-08-10 for the semantic-only
unique-target candidate before the next live attempt:

```bash
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
```

It covered locked dependency sync, Ruff, strict mypy across 149 source files,
research-package validation, schema and generated-document freshness, the full
pytest suite, and whitespace checks. The Release build command above separately
ran native fixture/conformance coverage before installation and the live run.
