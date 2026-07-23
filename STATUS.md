# Implementation status

## Works now

- Deterministic mock Kenshi-like environment implementing reset, observe, step,
  and close.
- Strict Pydantic schemas for telemetry, observations, decisions, bounded plans,
  plan patches, typed conditions, actions, receipts, and memories.
- Heuristic, scripted, subprocess, OpenAI Responses, and OpenRouter vision planners.
- Hosted structured output is scheduler-aware: it requests a decision, plan, or
  future-only patch according to current plan state. OpenAI Responses output
  tokens use a bounded base-plus-per-step budget, and condition paths are a
  schema enum rather than unconstrained strings.
- JSONL event logs, SQLite memory, replay summaries, schema export, and tests.
- Default-compatible `single_step` and feature-flagged `continuous` schedulers.
  In mock/fake environments, one strategic response can execute multiple
  guarded actions with causal postconditions, immediate precondition rechecks,
  bounded branches/retries/budgets, lifecycle replay, and plan metrics.
- Live continuous scheduling remains disabled by default. The only current
  exception is the native-assisted `food_procurement_v1` grammar, which binds
  one stable vendor through approach, exact dialogue, exact trade ownership,
  current tooltip inspection, and one exact-delta purchase. It additionally
  requires `--acknowledge-continuous-live`.
- Continuous mode has one cancellable observation pump and bounded authoritative
  store for monotonic revisions, telemetry-only ingest, visual carry-forward,
  deltas, transient events, isolated subscribers, active plan/step/command
  state, and command receipts with causal start/completion revisions.
- Portable continuous mode has an independent deterministic safety subscriber.
  It preempts blocked planner or action work on reflex, stale/stalled stream,
  pause-capability loss, an exact human-input event, or unauthorized unpause;
  uncertain dispatch is recorded conservatively, and cleanup is successful only
  after a later paused revision.
- Configured movement-pulse skills become stateful options in portable
  continuous mode. Their lifecycle and state-stream progress are replayable;
  one strategic advisory can overlap movement, and a matching future-only patch
  is withheld until post-option state, assumptions, and remaining budgets pass
  a second deterministic validation.
- Protocol `0.4.0` retains the `0.2.0` session-scoped opaque handle identities
  and `0.3.0` bounded causal native-command envelope. Caller-owned UUID command
  IDs, complete world revisions, control mode, identity session, exact
  selection, and exact target are checked before a player order; keyed
  acknowledgements report accepted, rejected, completed, or cancelled state.
  The additive fields expose in-game elapsed minutes, exact dialogue
  target/options, and current tooltip text/source bounds.
- The deterministic live-shaped P6 proof completes approach, dialogue, and
  inspection from one response, then an exact purchase from a second: four
  actions, two strategic calls, exact 649-cat debit, one added food item, and
  paused postconditions. The exact pinned `0.4.0` Release x64 DLL loaded in
  Kenshi and emitted strict fresh paused telemetry with the new capabilities;
  supervised action-chain validation remains pending.
- Hosted P6 dry preflight now returns a strict schema-valid world-phase plan in
  23.95 seconds at medium reasoning. The policy still withheld execution while
  remaining semantic issues were tightened. Live food planning may rebase only
  across sequence-only updates with an identical identity/capability/game/UI/
  native-command/selection/exact-vendor fence; generic stale outputs remain
  rejected. The active burn-in disables concurrent option advice because its
  pulse is shorter than measured hosted latency.
- Once the model proposes the exact phase action sequence, target, and typed
  arguments, trusted food-policy code compiles the canonical conditions,
  branches, timeouts, and risk budgets. Model-authored duplicated safety
  scaffolding is no longer relied upon; a wrong action structure is still
  rejected unchanged.
- After a long session froze the latest atomic snapshot at sequence 3985, the
  supervisor cancelled the planner with zero actions. The current protocol
  `0.4.0` hotfix makes sampler-latch release exception-safe and retries
  transient Windows replacement failures four times. Its 183,296-byte x64 DLL
  SHA-256 is
  `0096082215cbc1f842a8947291570328481c78cab9c23b8ae00a4dcdf6e888a3`;
  fresh relaunch samples advanced 61 -> 69 -> 78.
- A bounded per-journey action-outcome ledger that feeds each planner call its
  recent validated actions, material frame changes, telemetry/position deltas,
  and explicit no-op feedback.
- Windows client-area capture and SendInput controller behind two independent
  live-execution gates.
- Explicit `interface_only` and `native_assisted` control modes. Interface-only
  observations omit native command capabilities and skills; native-assisted
  live execution adds a dedicated configuration opt-in and CLI acknowledgement.
- Macro/skill expansion with action and rate limits.
- Native KenshiLib plugin source that hooks PlayerInterface::update and emits a
  partial, atomic telemetry snapshot from the game/UI thread.
- Reproducible Windows native toolchain verification and a successful
  Visual C++ 2010 SP1 `Release | x64` plugin build.
- RE_Kenshi 0.3.4/KenshiLib 0.4.0 installation and native plugin loading on
  Kenshi 1.0.68 Steam (running RE_Kenshi's supported 1.0.65 compatibility
  executable).
- Initial read-only field validation: schema-valid two-hertz snapshots, fresh
  UTC timestamps, one-character squad identity/selection, money, camera and
  character position, movement speed, pause, and speed multiplier.
- Stable-identity live boundary validation: exactly one selected ID agreed with
  the squad flag; 18 nearby characters had 18 IDs; four same-named Ninja Guards
  had four IDs; and a paused camera orbit changed camera state without changing
  the session, selection, or nearby ID set. Native list reordering was not
  observed in that run and remains an automated/source-level case.
- Causal-command live boundary validation: protocol `0.3.0` keyed and rejected
  one stale revision without movement, accepted one current exact
  selection/target request, retained its identity while pathing, completed only
  for exact-target dialogue, cleared active state, remained paused, and closed
  normally without a new plugin/renderer/Application error.
- Live crash triage: Kenshi's generic out-of-video-memory dialog corresponded
  to `DXGI_ERROR_DEVICE_REMOVED` with an internal-driver-error reason. The prior
  plugin DLL reproduced the same renderer reset after a ten-minute baseline,
  ruling out stable identity as a necessary cause. Low textures and disabled
  water reflections then passed a more-than-ten-minute identity soak and clean
  exit under continued memory pressure. Broad live stability remains open; see
  `docs/LIVE_STABILITY_INCIDENT_20260723.md`.
- Nearby-character and UI telemetry: names, factions, dispositions, world
  positions, trader capability, current world/inventory/dialogue/trade screen,
  and game-rendered viewport visibility with normalized screen positions. Live
  validation placed a visible goat at the matching upper-left screenshot point;
  the signal deliberately does not claim geometry-occlusion or clickability.
- Windows live dry-run validation: a bounded heuristic episode captured five
  isolated 1920x1080 Kenshi frames, paired them with fresh telemetry, logged
  four proposed actions, and withheld every input action at the safety gate.
- Structured-output live planning with lower-latency `gpt-5.6-luna` and optional
  OpenRouter latency routing, plus an active burn-in profile that limits
  execution to audited pause, overlay, map, inventory, focus, and movement skills.
- A flushed terminal decision stream and translucent always-on-top Windows
  viewer showing intent, concise rationale, action, confidence, planner latency,
  and execution result as the run unfolds.
- Foreground-safe, per-monitor-DPI-aware capture and scan-code keyboard input;
  a live pause probe changed plugin telemetry and restored the original state.
- Distinct fine-world and coarse-map movement skills with planner-visible
  preconditions and guard-enforced normalized pointer envelopes.
- Supervised live validation of both movement modes, including atomic absolute
  move-plus-click injection that remains exact during physical mouse movement.
- Executor-controlled fine and coarse movement pulses with fresh pause-state
  confirmation, F12 interruption, blocked direct unpause, and a 30-decision
  active profile.
- Planner-selected movement duration inside separate fine/coarse safety bounds,
  plus polite keyboard/mouse leases that wait for idle, restore foreground and
  cursor state, and yield on resumed human activity after guaranteed re-pause.
- A four-turn Luna live validation of planner-selected duration and polite input
  leasing: four executed actions, no stale observations/rejections/errors, and
  paused final telemetry; native probes restored exact focus/cursor handoff.
- Native Alt+Tab handoff validation confirmed Kenshi lost foreground focus before
  a saved pointer coordinate was restored, preventing edge-scroll camera input.
- A completed 30-decision Terra exploration episode with 16 movement pulses,
  no rejected actions or environment errors, and paused telemetry at every
  observation.

The supervised evidence above predates explicit run-level control-mode labels.
Treat the vendor-approach evidence as native-assisted and do not merge it with
new interface-only evidence.

## Still requires broader live validation

- Squad enumeration and state across recruit, dismiss, reorder, KO, death,
  save/load, and zone transitions.
- Safety-critical getting-eaten state; the raw KenshiLib byte produced a false
  positive on a healthy new character and is intentionally omitted.
- Repeated focus, client-coordinate, key-binding, and UI-scale calibration.
- Broader hosted vision-planner behavior beyond the safe movement burn-in.
- Comparative Luna/OpenRouter latency and action-quality trials from a fixed save.
- Repeated click-drift testing across resolutions, window modes, and UI scales.
- Screen-position validation across camera rotations, zoom levels, interiors,
  and partially occluded characters.
- Longer renderer-stability soaks on the current Intel integrated-GPU host,
  including zone transitions and ordinary gameplay rather than a paused save.

## Deliberately not implemented

- Arbitrary internal Kenshi actions or direct mutation of game state.
- Omniscient world-state extraction.
- Automatic save reloads or hidden reset commands.
- Unattended enabling of real keyboard/mouse injection.
- General live continuous execution, general option conversion, and arbitrary
  stateful live options. Strategic overlap and active patch application remain
  narrow. `single_step` and a disabled live policy remain the defaults;
  `food_procurement_v1` is the sole conditional live exception.
