# Architecture

The system separates observation, deliberation, action, memory, and evaluation
so failures can be attributed instead of blurred together.

```text
Kenshi process
  └─ KenshiLib plugin (game/UI thread)
       ├─ observational telemetry ──> atomic telemetry.latest.json
       └─ reviewed native command bridge (native_assisted only)

Python runtime
  ├─ telemetry reader ─────────┐
  ├─ triggered screenshot ─────┼─> observation pump
  ├─ SQLite memory ────────────┘          │
  │                                      v
  │                            bounded world-state store
  │                              ├─ latest + deltas/events
  │                              ├─ entity lifetimes
  │                              ├─ active plan/command
  │                              └─ subscriber queues
  │                                      ├─> safety supervisor
  │                                      │     └─ cancel + guarded safe pause
  │                                      └─> scheduler/executor
  ├─ reflex layer (shared deterministic pause/stop rules)
  ├─ planner (heuristic, scripted, subprocess, or vision LLM)
  ├─ schema + policy + rate-limit guard
  ├─ skill/macro compiler
  └─ executor
       ├─ interface_only ──> Windows SendInput ──> ordinary Kenshi UI
       └─ native_assisted ──> marked bounded bridge skills + Windows input

Every boundary ──> JSONL session log ──> replay and evaluation
```

## Environment contract

`reset()` establishes an episode and returns an observation. `observe()` is
side-effect free and requests a visual frame when capture exists.
`observe_without_capture()` supplies telemetry without forcing a new visual
frame. `step(action)` validates or executes one action, waits for a bounded
settle interval, and returns a receipt plus the next observation. `close()`
releases resources without manipulating the game.

## Continuous world-state stream

Only feature-flagged continuous mode creates the in-process
`WorldStateStore`. One cancellable `ObservationPump` reads the environment on a
configured cadence; consumers subscribe to the store rather than independently
polling the telemetry file. Publishing is synchronous within the asyncio event
loop, so validation, registry updates, journal writes, and subscriber fan-out
are one ordered operation.

The store:

- rejects regressing or state-conflicting revisions and reports telemetry
  sequence stalls;
- carries forward the last validated screenshot on telemetry-only updates;
- bounds snapshot history, semantic deltas, event journal, command history, and
  subscriber queues;
- retains transient observation events after the latest snapshot drops them;
- tracks capability epochs without converting unavailable data into absence;
- normalizes nearby ordinal IDs into process-local lifetime IDs using observed
  fingerprint and position evidence, while logging ambiguous matches;
- owns active plan, step, command ID, and causal start/completion revisions;
- provides `wait_for(..., after_revision=R)`, which cannot succeed from `R`.

This is an authoritative Python state stream over the plugin's existing atomic
latest-snapshot file. It is not a native event transport, and its entity IDs are
not validated Kenshi object handles. See `docs/ADR_WORLD_STATE_STREAM.md`.

## Independent safety supervision

Portable continuous mode starts one `SafetySupervisor` subscriber before the
observation pump. It evaluates deterministic reflexes, telemetry staleness,
consecutive sequence stalls, pause-capability withdrawal, and unexpected
unpause from immutable `StoreUpdate` snapshots. Each update carries the active
plan and command state that existed when it was published, so delayed
subscriber processing cannot retroactively reclassify an authorized action.

The scheduler races strategic planning and plan execution against the
supervisor's first latched preemption. A blocked task is canceled once. If
action delivery was already attempted, the executor spends its reservation and
records the command as inconclusive rather than risking an automatic duplicate.
Cleanup uses only `PauseAction(paused=true)`, still passes control-mode and
allowlist policy, and may bypass only the ordinary rate counter so exhaustion
cannot prevent an emergency pause. A cleanup terminal is `safe_paused` only
after a later capable world revision confirms pause; otherwise it is explicitly
failed or unverified.

This portable implementation does not enable live continuous mode and does not
measure Windows F12, human-input, or controller cancellation latency. See
`docs/ADR_SAFETY_SUPERVISOR.md`.

## Partial observability

Telemetry carries an explicit capability list. The planner must not interpret a
missing field as zero. Exact hidden faction values, distant entities, complete
map data, and mechanical formulas should remain unavailable unless the player
could reasonably observe them.

## Action hierarchy

Primitives are pause, speed, wait, key, hotkey, cursor move, and click. Skills
expand into bounded primitive sequences. In `single_step` the LLM chooses one
action; in `continuous` it may propose a bounded typed plan. It never
micromanages primitive input timing. Reflexes may pause or stop, but broad
autonomy stays with the planner.

Every run has a typed control mode. `interface_only` is the default and filters
native command capabilities and marked skills before planning; the guard and
environment reject them again at execution boundaries. `native_assisted`
requires a configuration opt-in plus a dedicated CLI acknowledgement before
live execution. Observations, receipts, lifecycle events, overlays, summaries,
and metrics retain the mode.

## Native boundary

The plugin owns no model logic. Its observational path serializes a versioned,
partial snapshot at a low fixed frequency. It hooks a known main/UI-thread
update point, calls the original function, samples only validated fields, and
writes an atomic file. The Python process never loads Kenshi memory directly.

The plugin also contains one reviewed `PLAYER_TALK_TO` command bridge used by
`approach_confirmed_vendor`. This is not described as read-only or UI-only. It
is marked `requires_native_assisted` in the macro schema and unavailable in the
default mode. See `docs/ADR_CONTROL_MODES.md`.

## Failure attribution

Logs distinguish observation errors, planner errors, policy rejection, input
execution, and observed outcome. A benchmark result should therefore say
whether the agent misunderstood the world, chose poorly, failed to operate the
UI, or lacked sufficient telemetry.
