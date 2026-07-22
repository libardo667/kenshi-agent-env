# Implementation status

## Works now

- Deterministic mock Kenshi-like environment implementing reset, observe, step,
  and close.
- Strict Pydantic schemas for telemetry, observations, decisions, actions,
  receipts, and memories.
- Heuristic, scripted, subprocess, and optional OpenAI vision planners.
- JSONL event logs, SQLite memory, replay summaries, schema export, and tests.
- Windows client-area capture and SendInput controller behind two independent
  live-execution gates.
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
- Windows live dry-run validation: a bounded heuristic episode captured five
  isolated 1920x1080 Kenshi frames, paired them with fresh telemetry, logged
  four proposed actions, and withheld every input action at the safety gate.
- OpenAI structured-output live planning with `gpt-5.6-terra`, plus an active
  burn-in profile that limits execution to audited pause, overlay, map,
  inventory, and focus skills.
- Foreground-safe, per-monitor-DPI-aware capture and scan-code keyboard input;
  a live pause probe changed plugin telemetry and restored the original state.
- Distinct fine-world and coarse-map movement skills with planner-visible
  preconditions and guard-enforced normalized pointer envelopes.
- Supervised live validation of both movement modes, including atomic absolute
  move-plus-click injection that remains exact during physical mouse movement.

## Still requires broader live validation

- Squad enumeration and state across recruit, dismiss, reorder, KO, death,
  save/load, and zone transitions.
- Safety-critical getting-eaten state; the raw KenshiLib byte produced a false
  positive on a healthy new character and is intentionally omitted.
- Repeated focus, client-coordinate, key-binding, and UI-scale calibration.
- Broader OpenAI vision-planner behavior beyond the safe overlay burn-in.
- Repeated click-drift testing across resolutions, window modes, and UI scales.

## Deliberately not implemented

- Direct mutation of Kenshi internals for actions.
- Omniscient world-state extraction.
- Automatic save reloads or hidden reset commands.
- Unattended enabling of real keyboard/mouse injection.
