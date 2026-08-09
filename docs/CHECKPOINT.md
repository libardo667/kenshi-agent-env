# Checkpoint: independent Kenshi work channels

This checkpoint records the coherent Protocol 1.20 task-channel slice.
Source-proven, test-proven, live-proven, and withheld conclusions remain
separate.

## Repository and authority

```text
parent commit          89457e06a47038ebc58e808f03ceb3d8703611de
integration branch     main
starting remote        origin/main at 5753bcb8ca993d46964fc35349338e1f91855bce
starting tree          clean
producer protocol      1.20.0
request schema         1.4
loaded capabilities    48
```

Recent `main` authority was inspected before editing. `89457e0` owns the real
player-topology migration and its live proof; `5753bcb` accepts the strict
Protocol 2.0 world-model decision; `70f6a65` makes research evidence a checked
contract; `04855dc` owns the current documentation truth audit; and `2686719`
owns the complete portable gate.

The current producer authority is `TelemetrySnapshot` and
`CharacterWorkState` in `src/kenshi_agent/core/telemetry.py`, native
serialization in `native/KenshiAgentTelemetry/KenshiAgentTelemetry.cpp`, the
capability manifest, and generated schema. The inspected Kenshi boundary is
owned by `game_sources/research/task_channels`. The Protocol 2.0 decision keeps
authority over the future plural controller registry, not these now-landed
Kenshi work channels.

## Coherent 1.20 slice

Protocol 1.20 replaces `CharacterTaskState` with one nullable character `work`
record containing:

- `ordinary_orders`, Kenshi's player order queue;
- `jobs` and the separate `jobs_enabled` switch;
- `permanent_jobs`, sourced from KenshiLib's `permajobs`; and
- `current_activity`, sourced from the task system's current goal.

Each retained collection owns `items`, `completeness`, and nullable
`known_total`. Every task entry owns nullable value/name, subject, description,
and position evidence. Unknown facts remain null. Current activity has no queue
position. The ordinary queue is complete only when the available source proves
empty or exactly one entry; a larger queue is a bounded first/second/tail sample
with unknown total and unknown tail position.

The superseded `task_state`, `orders_count`, `orders_complete`, `jobs_count`,
`jobs_complete`, `permajobs`, and cross-channel task-name ownership inference
were deleted from producer, consumers, fixtures, monitoring, schemas, and
generated documentation. There is no old/new reader. Monitoring observes fresh
ordinary work as unattributed unless causal evidence exists; Jobs, permanent
Jobs, and activity never prove a controller-issued order survived.

Portable models, native fixtures, capabilities, schemas, generated
documentation, the proof ledger, research index, and current documentation move
together with this authority.

## Source-proven

- KenshiLib 0.4.0 `AITaskSystem.h` separately declares `orders`, `jobs`,
  `permajobs`, `doJobsEnabled`, and `currentGoal`, with separate
  `hasPlayerOrders()`, `isJobsEnabled()`, and `getCurrentGoal()` readers.
- `ActionDeque` exposes first, second, and last task accessors plus empty and
  one-item predicates. It exposes no size reader. A multi-entry ordinary total
  and sampled tail position therefore cannot be claimed.
- `Tasker.h` exposes task key, subject handle, and description. An invalid or
  targetless subject cannot be distinguished by that read and remains null.
- ForgottenGUI `OrdersPanel.h` carries indexed/enabled Job rows and Job move and
  removal calls. It supports configured Jobs as a distinct UI concept but does
  not own ordinary orders or current activity.
- Exact header, library, include-tree, executable hashes, RVAs, declarations,
  and native call sites are recorded under
  `game_sources/research/task_channels`.

These declarations and call sites prove available structures and the
implemented read path. They do not prove the live game published a particular
state.

## Test-proven

- The strict 1.20 model and shared native telemetry fixture represent one
  retained ordinary `OPERATE_MACHINERY` order beside a separate current
  activity while Jobs and permanent Jobs are complete and empty.
- Negative tests reject the entire superseded `task_state` shape and
  contradictory completeness/total claims.
- Tests preserve null subjects and positions, an unknown multi-entry ordinary
  total, and a sampled tail with null position.
- Lifecycle tests prove a Job or activity cannot retain a controller order, and
  that fresh ordinary work without causal linkage is
  `observed_unattributed_work`, not fabricated ownership.
- The Release x64 conformance executable passed the shared native telemetry,
  Protocol 2.0, research, and native-command fixtures.

Fixtures and passing calls prove contracts and compiled behavior, not a changed
game world.

## Native build and installed provenance

The Release x64 VS2010 SP1 build completed and its native conformance executable
reported `Native protocol fixtures and semantics passed.` The exact built DLL
was installed before relaunching Kenshi.

```text
built DLL path          C:\Users\levib\AppData\Local\KenshiAgent\build\native\bin\KenshiAgentTelemetry.dll
built DLL sha256        3746e1dfd1feb9564c4a539388790b7d3f7f19e3971d965c08b223e8766b0d2f
built DLL size          414208 bytes
installed DLL path      C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll
installed DLL sha256    3746e1dfd1feb9564c4a539388790b7d3f7f19e3971d965c08b223e8766b0d2f
installed DLL size      414208 bytes
conformance exe sha256  08c960a8b4e0049f4bc42857157922736a8ce500e6dcaabbf51ae16c4f028a95
conformance exe size    284160 bytes
built equals installed  YES
provenance chain        consistent; protocol 1.20.0; generated header current; 48 capabilities present
```

## Live-proven

Exact bundle: `runs/task-channels-20260809T172100Z/manifest.json`.

The ignored local bundle indexes the exact request plus every decisive raw
frame-and-telemetry snapshot:

- pre-dispatch sequence 184 was fresh 1.20 telemetry, loaded and paused, with
  Paste selected and primary; both characters had complete empty ordinary
  queues, Jobs, and permanent Jobs, and null current activity;
- request `cmd-536652d87c7b42e59187dc55c2278963`, authored from fresh sequence 279,
  selected exact Paste and exact observed Iron Resource for context action
  `operate`;
- the diagnostic helper wrote the request but could not send its optional
  Windows hotkey from WSL. The native file-change watcher independently
  processed it, so no hotkey receipt is claimed;
- acknowledgement sequence 280 reported `completed/context_task_started`; that
  return is delivery evidence, not the live conclusion;
- later engine snapshot sequence 329 showed Paste's complete ordinary queue
  retaining one `OPERATE_MACHINERY` entry at position 0 while
  `current_activity` separately reported `OPERATE_MACHINERY` with null position;
- in that same later frame, Jobs and permanent Jobs both remained complete with
  known total 0. No Job was fabricated from the ordinary order or activity;
  and
- after bounded recovery, final sequence 383 was loaded, paused, modal-free,
  and had no active native command. The retained ordinary order and separate
  activity remained visible, with both Job channels still empty.

The exact subject happened to be known in this run and remained the resource
entity ID. The live run therefore does not prove an unknown subject case.

Final live disposition: **Kenshi remains loaded, paused, modal-free, with no
active native command. Paste's retained ordinary order and separate current
activity remain observable; Jobs and permanent Jobs remain empty; built and
installed DLL hashes match.**

## Withheld and named follow-on work

- **Exact command-to-order ownership:** matching task identity and target do not
  form a general causal correlator. The current monitor reports ordinary work as
  unattributed unless future native command records establish the link.
- **Plural native command registry:** simultaneous retained controller records,
  overlap, recipient supersession, and independent terminal histories remain
  Protocol 2.0 work.
- **Deep ordinary queue live probe:** no live multi-entry queue was authored, so
  unknown totals and sampled tail positions remain source- and test-proven.
- **Unknown subject live probe:** no invalid or targetless live task subject was
  observed; null preservation remains source- and test-proven.
- **Job lifecycle probe:** ordinary order, configured Job, permanent Job, and
  activity are separately representable now. Longer transitions among them are
  worthwhile named evidence work, not part of this bounded slice.

The earlier `player-topology-20260809T161112Z` bundle remains the separate live
authority for player topology. This task-channel run does not broaden or
reinterpret it.

## Verification

The complete portable gate passed over the final candidate with:

```bash
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
```

It covers locked dependency sync, Ruff, strict mypy, research-package
validation, schema and generated-document freshness, the complete pytest suite,
and `git diff --check`. The completion claim is valid only together with
the successful gate output for this checkout; if the candidate changes, the
gate must be rerun.
