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

## Must be verified on a real Kenshi installation

- Loading the staged native plugin with RE_Kenshi for the first time.
- Runtime compatibility with the user's Kenshi executable and active mod set.
- Every native field accessor, especially squad enumeration and virtual calls.
- Kenshi key bindings, window focus behavior, client coordinates, and UI scale.
- Screenshot interpretation and live planner behavior.

## Deliberately not implemented

- Direct mutation of Kenshi internals for actions.
- Omniscient world-state extraction.
- Automatic save reloads or hidden reset commands.
- Unattended enabling of real keyboard/mouse injection.
- Claims that the native plugin is field-tested in game.
