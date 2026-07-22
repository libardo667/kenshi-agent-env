# Architecture

The system separates observation, deliberation, action, memory, and evaluation
so failures can be attributed instead of blurred together.

```text
Kenshi process
  └─ KenshiLib telemetry plugin (read-only, game/UI thread)
       └─ atomic telemetry.latest.json

Python runtime
  ├─ telemetry reader ─────────┐
  ├─ client-area screenshot ──┼─> Observation
  ├─ SQLite memory ────────────┘
  ├─ reflex layer (pause/stop only by default)
  ├─ planner (heuristic, scripted, subprocess, or vision LLM)
  ├─ schema + policy + rate-limit guard
  ├─ skill/macro compiler
  └─ Windows SendInput executor ──> ordinary Kenshi UI

Every boundary ──> JSONL session log ──> replay and evaluation
```

## Environment contract

`reset()` establishes an episode and returns an observation. `observe()` is
side-effect free. `step(action)` validates or executes one action, waits for a
bounded settle interval, and returns a receipt plus the next observation.
`close()` releases resources without manipulating the game.

## Partial observability

Telemetry carries an explicit capability list. The planner must not interpret a
missing field as zero. Exact hidden faction values, distant entities, complete
map data, and mechanical formulas should remain unavailable unless the player
could reasonably observe them.

## Action hierarchy

Primitives are pause, speed, wait, key, hotkey, cursor move, and click. Skills
expand into bounded primitive sequences. The LLM chooses intent and one action;
it does not micromanage input timing. Reflexes may pause or stop, but broad
autonomy stays with the planner.

## Native boundary

The plugin owns no model logic. It serializes a versioned, partial snapshot at a
low fixed frequency. It hooks a known main/UI-thread update point, calls the
original function, samples only validated fields, and writes an atomic file.
The Python process never loads Kenshi memory directly.

## Failure attribution

Logs distinguish observation errors, planner errors, policy rejection, input
execution, and observed outcome. A benchmark result should therefore say
whether the agent misunderstood the world, chose poorly, failed to operate the
UI, or lacked sufficient telemetry.
