# Implementation status

## Works now

- Deterministic mock Kenshi-like environment implementing reset, observe, step,
  and close.
- Strict Pydantic schemas for telemetry, observations, decisions, bounded plans,
  plan patches, typed conditions, actions, receipts, and memories.
- Heuristic, scripted, subprocess, OpenAI Responses, and OpenRouter vision planners.
- JSONL event logs, SQLite memory, replay summaries, schema export, and tests.
- Default-compatible `single_step` and feature-flagged `continuous` schedulers.
  In mock/fake environments, one strategic response can execute multiple
  guarded actions with causal postconditions, immediate precondition rechecks,
  bounded branches/retries/budgets, lifecycle replay, and plan metrics.
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
- The portable nearby-entity registry preserves observed lifetimes across
  ordinal reordering using fingerprint and position evidence, records ambiguous
  matches, and does not infer disappearance while the entity-list capability is
  unavailable. It is not a native stable-handle claim.
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

## Deliberately not implemented

- Arbitrary internal Kenshi actions or direct mutation of game state.
- Omniscient world-state extraction.
- Automatic save reloads or hidden reset commands.
- Unattended enabling of real keyboard/mouse injection.
- Live continuous execution, general option conversion, and stateful live
  movement options. Strategic overlap and active patch application currently
  exist only for the portable configured-movement adapter. `single_step`
  remains the default and the continuous path is restricted to mock/fake
  environments.
- Native stable entity handles and bridge-level command acknowledgement. The
  current in-process registry and command IDs establish portable causal
  semantics but do not make the native plugin protocol P5-complete.
