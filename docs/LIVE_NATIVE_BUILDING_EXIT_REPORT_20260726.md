# Native building-exit live report — 2026-07-26

## Outcome

Protocol `0.7.0` adds the no-argument planner action
`exit_current_building`. The model supplies no door, direction, bearing, or
coordinates. Native code binds the one selected indoor character, resolves the
nearest valid unlocked door from the current building, and issues one ordinary
`MOVE_CUS_ORDERED` order to that door's outside point.

Run `20260726Tnative-building-exit-live-04` is the green live proof:

- selected Hep began paused and `indoors=true` at
  `(-51182.90, 1581.632, 2578.61)`;
- command `cmd-fd4a3d1171d743488d8e58fa905f19de` was based on telemetry
  sequence 494 and accepted at 495;
- the continuous controller deterministically unpaused the paused world after
  acceptance;
- Hep moved about 155.58 x/z world units to
  `(-51285.13, 1563.35, 2694.46)`;
- native completion published at sequence 506 with
  `reason=outside_door_destination_reached`;
- the keyed monitored option and controller-verified plan step succeeded;
- run-finished safety confirmed pause at sequence 508.

The user directly observed Hep outside. Kenshi nevertheless continued to report
`Character::isIndoors().isValid()==true`. That flag is therefore useful for
authorizing an exit request from this interior, but is not an authoritative
terminal for this doorway.

## Correction history

- `...live-01`: two orders were accepted then cancelled `world_paused`.
  This exposed the mismatch between run-finished auto-pause and the long-form
  profile's expected running world.
- `...live-02`: the controller owned the paused-start unpause and moved Hep
  about 149.60 units, but an indoor-building-handle change was classified as
  success too early.
- `...live-03`: stable-outdoor membership replaced handle-change completion.
  Hep visibly exited and moved about 176.98 units, but the lingering indoor
  handle caused an honest bounded `movement_stalled` terminal after ten
  unpaused seconds.
- `...live-04`: completion accepted either stable outdoor membership or
  tightly reaching the native-resolved outside-door point after meaningful
  movement. This matched the visible result and removed `step stopped`.

The shared stall monitor remains active for genuine blocked paths. A reached
outside-door point uses a three-unit terminal tolerance and requires at least
one unit of movement from the indoor origin, so an unexecuted order cannot pass
from its start state.

## Artifact and portable evidence

The installed 205,824-byte DLL has SHA-256
`2110dcf73421a5919e5c3f0efb44cdd9929946a0902aa09d8662191cd94ba8d9`.
Built, staged, and installed copies were verified byte-identical. The prior
205,312-byte DLL is recoverable under
`%LOCALAPPDATA%\KenshiAgent\backups\native\20260726T143306Z-pre-exit-destination-terminal-0.7.0`.

The live run contains 271 JSONL events. Its event log SHA-256 is
`03556bdbf74d91d4447e6b0e8339f381611bb30c21a735c109b996c093fb0010`.
Its two retained frame hashes are
`64994683f55498ca77222ee15c2d1d6ddd3fadb64ddbbde37b9fa9b93662fec8`
and
`6dbfec69d53f94758175850037be0084c2aa86225dafd99caf35e6ba79b1e0c1`.

Portable verification passed 531 tests, Ruff, strict mypy over 58 source files,
and regenerated schemas. The pinned VS2010 SP1 Release x64 build and its native
protocol/movement conformance executable passed before installation.

## Evidence boundary

This proves one Storm House bar exit, one selected character, one open unlocked
door, and the exact installed build. Locked doors, multiple exits, damaged or
disabled doors, other building types, and broader indoor-state semantics remain
unproven. Named regional/map travel remains a separate missing action surface.
