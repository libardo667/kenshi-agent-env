# Checkpoint: native productive mining and local trade

This goal makes the mine-to-vendor loop native, efficient, and truthful before
returning to an open-ended survival soak. Productive resource work owns its
monitored interval and fastest playback. Trade-window authoring requires a
local primary/vendor pair before native rendering, and an accepted request is
not success until Kenshi reports the exact open-window terminal.

## Repository and authority

```text
parent commit          dd1230a357b2cac8ac88b08002795fc6459c94d4
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
  admission, waits for real output stock, and releases controller-owned work.
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

## DLL artifacts and exact commands

Both sides of the installation are preserved:

```text
pre-change DLL path     C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-native-mining-local-trade\pre-change\KenshiAgentTelemetry.dll
pre-change DLL sha256   033cf6e489816644f5310eb38d90ffc4e625e4812f8ed41d79aac05d58e4dfdd
pre-change DLL size     435712
pre-change PDB sha256   9976a2e536e838ecc635ed2b29504b38fd9f686475530ab185c1c5ddaa2fd4d6
pre-change PDB size     11127808
replacement DLL path    C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-native-mining-local-trade\replacement\KenshiAgentTelemetry.dll
replacement DLL sha256  76310e286fc4caf833ee865e0c68c01c25f4382cbbbc39db92e5640050b08f62
replacement DLL size    436736
replacement PDB sha256  9787a2082b3e1172902920e8750737a205be8aaca4f10a51b4c89034dcde1ff7
replacement PDB size    11127808
conformance exe sha256  3f15a37919e1223034c4e4783833d34286a0f803073d3411758da22f21406a97
conformance exe size    318464
installed parity        YES
```

Rollback exactly to the pre-change DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-native-mining-local-trade\pre-change\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Build the candidate:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build_native.ps1
```

Reinstall the exact live-proven replacement DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-native-mining-local-trade\replacement\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
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

The later 100+ turn open-ended survival soak remains outside this bounded goal.

## Verification

The complete portable gate passed on 2026-08-10:

```bash
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
```

It covered locked dependency sync, Ruff, strict mypy across 149 source files,
research-package validation, schema and generated-document freshness, the full
pytest suite, and whitespace checks. The Release build command above separately
ran native fixture/conformance coverage before installation and the live run.
