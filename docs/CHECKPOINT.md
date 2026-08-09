# Checkpoint: atomic Protocol 2.0 cutover

This checkpoint replaces the Protocol 1.21 resource-operator checkpoint. The
repository, native producer, installed DLL, Python consumers, replay and
scenario tooling, fixtures, schemas, generated docs, and tests now share one
strict Protocol 2.0 contract.

## Repository and authority

```text
parent commit          1d53e57e787309975e75b710eba96b22d1feb12d
integration branch     main
starting remote        origin/main at 1d53e57e787309975e75b710eba96b22d1feb12d
starting tree          clean
producer protocol      2.0.0
request schema         1.4
loaded capabilities    49
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

## Evidence lanes

Source-proven and test-proven: the strict 2.0 producer and model cutover,
plural command consumers, no aliases, the temporary producer limit and dated
deletion requirement, the complete-order invariant, and shared C++/Python
fixtures. The native Release x64 conformance executable reported
`Native protocol fixtures and semantics passed.`

Installed-proven: Kenshi was not running during replacement. The built,
preserved 2.0, and installed DLL hashes are identical. The provenance checker
found protocol 2.0.0 and all 49 declared capabilities in the installed DLL.

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
`docs/reconstruction/protocol_2_cutover.json`.

```text
old 1.21 backup sha256  91526b828e44035b0cb6de5a22b7cc5ad0c2e392b66a7b8adcbf9ae9403d8db8
new 2.0 DLL sha256      ac96f7e6b41edcff17f8c007ab7dc41b639ccbba82ea96aee7318b41ceb9bf1d
new installed parity   YES
conformance exe sha256 ed0a916277e235ca07db2691d8a48f32f49e75b3951cbd30672cf2f90bc63048
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

The final candidate is required to pass:

```bash
UV_CACHE_DIR=/tmp/kenshi-uv-cache ./dev verify-portable
```

That gate covers locked dependency sync, Ruff, strict mypy, research-package
validation, schema and generated-document freshness, the complete pytest suite,
and `git diff --check`.
