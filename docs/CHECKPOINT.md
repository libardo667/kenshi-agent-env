# Checkpoint: Protocol 2.0 interface-lifecycle hardening

This checkpoint follows the atomic Protocol 2.0 cutover. The first open-ended
soak exposed a Prospecting window left over from a completed survey, an opened
dialogue with no modeled exit, and completed no-op plans whose frame churn hid
semantic non-progress. This slice fixes that class across the native producer,
operation registry, planner surface, mocks, replay, tooling, evidence, generated
artifacts, and tests.

## Repository and authority

```text
parent commit          38d4437543c549a016ff15f434f3691dba72b396
integration branch     main
starting remote        origin/main at 1d53e57e787309975e75b710eba96b22d1feb12d
starting tree          clean
producer protocol      2.0.0
request schema         1.4
declared capabilities  50
```

The authority is `docs/PROTOCOL_2_WORLD_MODEL_DECISION.md`, the strict models
in `src/kenshi_agent/core/telemetry.py`, and the producer in
`native/KenshiAgentTelemetry/KenshiAgentTelemetry.cpp`. There is no 1.x
compatibility reader. `TelemetrySnapshot.squad`, the `native_control` wire
object, `NativeControlState.active_command_id`, and the `acknowledgements` wire
collection are rejected instead of translated.

## Coherent 2.0 slice

- Player topology uses explicit roster, platoon membership, selection,
  primary, and active-platoon authorities.
- Controller state is always consumed as the plural
  `controller_commands.commands` collection. The native producer may publish
  at most one retained record only until **2026-09-20**; no Python consumer is
  allowed to assume that cardinality.
- Ordinary orders, Jobs, permanent Jobs, and current activity remain separate
  work channels.
- When ordinary-order enumeration is complete, `has_player_orders` must agree
  with the exact total: true forbids total zero and false forbids a positive
  total. Truncated or unknown enumeration does not invent a contradiction.
- Planner context, observations, run summaries, mocks, replay, fixtures,
  scenario tooling, schemas, and generated documentation use only the 2.0
  names and shapes.

## Interface-lifecycle and non-progress fix

- `survey_local_resources` copies its readings, hides the exact Prospecting
  window it opened, and refuses terminal success if the window remains visible.
- `close_active_interface` is the sole native UI-exit operation. It closes
  Prospecting, dialogue, message boxes, trade and inventory windows, character
  stats, management screens, and ordinary GUI windows, then verifies the
  blocking signals are absent before completing.
- The exit is planner-visible whenever fresh telemetry proves a blocking
  interface and remains wire-valid with an empty selection because it addresses
  game-wide UI state rather than a character recipient.
- Every modeled interface EXIT row is covered by a native operation; the audit
  contains no stranding gaps and no pixel-based covered route.
- Three completed observe-only plans against the same actionable UI signature
  terminate with `planner_non_progress`. Frame, sequence, clock, playback, and
  command-record churn do not reset the bound; a completed real-work operation
  does.
- The manual native dispatcher reads the plural
  `controller_commands.commands` authority and exposes every request field; it
  contains no deleted `native_control` reader.
- Mutable repository call-site line numbers are informational. Current-source
  continuity uses path, enclosing function, and contained expression, while the
  recorded source SHA remains provenance for the originally inspected blob.

## Evidence lanes

Source-proven and test-proven: the strict 2.0 producer and model cutover,
plural command consumers, no aliases, the temporary producer limit and dated
deletion requirement, the complete-order invariant, and shared C++/Python
fixtures. The interface-lifecycle fix is pinned by shared empty-selection close
fixtures, an all-interface exit audit, the exact dialogue-plus-modal affordance
regression, and bounded non-progress tests. The native Release x64 conformance
executable reported `Native protocol fixtures and semantics passed.`

Installed-proven: Kenshi was not running during replacement. The current build,
preserved fixed 2.0 artifact, and installed DLL hashes are identical. The
pre-fix installed 2.0 artifact is separately preserved for exact rollback.

Live-proven remains deliberately separate. This cutover did not launch Kenshi
and does not claim a fresh 2.0 game-process load or command outcome. Historical
live conclusions retain reduced committed evidence artifacts containing their
manifest facts, decisive frames, request/acknowledgement, omitted-file hashes,
and final disposition. Those artifacts preserve historical conclusions; they
do not upgrade them to Protocol 2.0 live proof.

The README also withholds true total-party-loss recovery: elective nearby-body
shifting is supported and empty-roster authoring is allowed, but a live
end-to-end recovery after true total party loss remains unproven.

## DLL artifacts and reinstall commands

The exact machine-local artifact record, hashes, sizes, and commands are in
`docs/reconstruction/protocol_2_cutover.json` for the breaking cutover and
`docs/reconstruction/interface_lifecycle_soak_regression.json` for this fix.

```text
old 1.21 backup sha256  91526b828e44035b0cb6de5a22b7cc5ad0c2e392b66a7b8adcbf9ae9403d8db8
pre-fix 2.0 DLL sha256 ac96f7e6b41edcff17f8c007ab7dc41b639ccbba82ea96aee7318b41ceb9bf1d
fixed 2.0 DLL sha256   f68f63889ad29bcb63a267bdbd746f56f200357dda193e58fee608223fa68913
fixed installed parity YES
conformance exe sha256 643d6ee007dc3b49b3e63a49de559e4b129ac684bcc4063bf83079252089b676
```

Build:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build_native.ps1
```

Install the current build:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\build\native\bin\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Reinstall the preserved 2.0 artifact:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260809-protocol-2-cutover\protocol-2.0\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Reinstall this fixed 2.0 artifact:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-interface-lifecycle\fixed\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Rollback only the interface-lifecycle fix while retaining Protocol 2.0:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-interface-lifecycle\pre-fix\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Rollback to the preserved 1.21 artifact:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260809-protocol-2-cutover\pre-2.0\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

## Withheld and named follow-on work

- **By 2026-09-20:** replace the native singleton publication bridge with the
  full retained-command registry and delete its `at most one` exception.
- Fresh Protocol 2.0 live-load and multi-command causal bundles remain
  follow-on proof, not an assertion of this source/test cutover.
- True total-party-loss body-shift recovery remains unproven.
- Cross-session character identity and deeper unknown/truncated task-channel
  enumeration remain bounded exactly as before.

## Verification

The final candidate passed on 2026-08-10:

```bash
UV_CACHE_DIR=/tmp/kenshi-uv-cache ./dev verify-portable
```

That gate covers locked dependency sync, Ruff, strict mypy, research-package
validation, schema and generated-document freshness, the complete pytest suite,
and `git diff --check`.
