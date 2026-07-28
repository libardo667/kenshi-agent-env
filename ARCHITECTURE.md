# Architecture

The system separates observation, deliberation, action, memory, and evaluation so
failures can be attributed instead of blurred together.

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
  │                              ├─ latest + valued deltas/events
  │                              ├─ entity lifetimes
  │                              ├─ active plan/command
  │                              └─ subscriber queues
  │                                      ├─> safety supervisor
  │                                      │     └─ cancel + guarded safe pause
  │                                      └─> scheduler/executor
  │                                             ├─ semantic action contracts
  │                                             └─ monitored movement options
  │                                                   ↕ future/change-course patch
  ├─ reflex layer (shared deterministic pause/stop rules)
  ├─ planner (heuristic, scripted, subprocess, or vision LLM)
  ├─ strategic advisor (read-only hosted model + attributed guide corpus)
  │     └─ planner context only; no environment or controller authority
  ├─ schema + policy + rate-limit guard
  ├─ skill/macro compiler
  └─ executor
       ├─ interface_only ──> Windows SendInput ──> ordinary Kenshi UI
       └─ native_assisted ──> marked bounded bridge skills + Windows input

Every boundary ──> JSONL session log ──> lifecycle analysis and evaluation
```

Full environment replay is a separate logging level. Default compact observation
digests keep long live logs bounded and support lifecycle analysis, but
`ReplayEnvironment` requires `runtime.log_full_observations: true`.

## Environment contract

`reset()` establishes an episode and returns an observation. `observe()` is
side-effect free and requests a visual frame when capture exists.
`observe_without_capture()` supplies telemetry without forcing a new frame.
`dispatch(action, command=...)` is the causal execution seam: the runtime supplies
one globally unique command ID and complete based-on revision, and the receipt
binds the result to the later observation. `step(action)` is the legacy primitive
beneath that seam. `close()` releases resources without manipulating the game;
the runtime's final-state owner verifies pause across normal, budget, failure,
cancellation, exception, and completion exits before environment close.

## Action hierarchy

Raw keys, hotkeys, cursor moves, clicks, and scrolls are controller primitives,
and the generic live planner cannot author them. Run control is separately typed
as noop, wait, pause/speed, and whole-run stop. Everything else is a reusable
semantic action binding current telemetry references through one catalog — the
[generated action catalog](docs/generated/ACTION_CATALOG.md) is the authoritative
list.

Each `ActionContract` owns planner visibility, capability and control-mode
requirements, pointer class, native requirement, risk cost, idempotency,
reference binding, execution route, receipt kind, and required verification
paths. The planner composes these in a bounded continuous plan; it never
micromanages primitive timing or coordinates. Legacy skills still expand into
bounded primitives for compatibility and calibrated transport.

This is the intended single source of action truth, not yet complete executable
truth. Contracts marked `controller_verified` — camera recovery, building exit,
contextual operation — return a typed terminal verdict from their owning
subsystem, so the planner supplies no success predicate. Extending that set is
the ongoing work.

## Partial observability

Telemetry carries an explicit capability list. **The planner must not read a
missing field as zero.** Hidden faction values, distant entities, complete map
data, and mechanical formulas stay unavailable unless a player could reasonably
observe them.

## Failure attribution

Logs distinguish observation errors, planner errors, policy rejection, input
execution, and observed outcome, so a benchmark result can say whether the agent
misunderstood the world, chose poorly, failed to operate the UI, or lacked
telemetry.

## Where each boundary is decided

| Boundary | Record |
| --- | --- |
| Control modes and what each permits | [ADR_CONTROL_MODES](docs/ADR_CONTROL_MODES.md) |
| Scheduler contract and plan authority | [ADR_CONTINUOUS_PLANNING](docs/ADR_CONTINUOUS_PLANNING.md) |
| Revision ownership and causal confirmation | [ADR_WORLD_STATE_STREAM](docs/ADR_WORLD_STATE_STREAM.md) |
| Final in-lease authorization fence | [ADR_INPUT_BOUNDARY_AUTHORITY_V2](docs/ADR_INPUT_BOUNDARY_AUTHORITY_V2.md) |
| Transactional global and plan budgets | [ADR_TRANSACTIONAL_ACTION_BUDGETS](docs/ADR_TRANSACTIONAL_ACTION_BUDGETS.md) |
| Pointer calibration identity | [ADR_CALIBRATION_IDENTITY](docs/ADR_CALIBRATION_IDENTITY.md) |
| Movement options and active-step interruption | [ADR_ACTIVE_OPTION_INTERRUPTION](docs/ADR_ACTIVE_OPTION_INTERRUPTION.md) |
| Independent preemption and safe pause | [ADR_SAFETY_SUPERVISOR](docs/ADR_SAFETY_SUPERVISOR.md) |
| Native handle identity and lifecycle | [ADR_STABLE_NATIVE_IDENTITY](docs/ADR_STABLE_NATIVE_IDENTITY.md) |
| Native request/acknowledgement causality | [ADR_CAUSAL_NATIVE_COMMANDS](docs/ADR_CAUSAL_NATIVE_COMMANDS.md) |
| How far the plug-in may go | [ADR_NATIVE_INTEGRATION_SCOPE](docs/ADR_NATIVE_INTEGRATION_SCOPE.md) |
| Read-only advisor boundary | [ADR_STRATEGIC_ADVISOR](docs/ADR_STRATEGIC_ADVISOR.md) |
| Reporting a missing capability | [ADR_RUNTIME_AFFORDANCE_REQUESTS](docs/ADR_RUNTIME_AFFORDANCE_REQUESTS.md) |
| Action-outcome continuity between calls | [ADR_ACTION_LEDGER](docs/ADR_ACTION_LEDGER.md) |
| Exact entity memory retrieval | [ADR_ENTITY_SCOPED_MEMORY](docs/ADR_ENTITY_SCOPED_MEMORY.md) |
| Continuity authority and commit timing | [ADR_CONTINUITY_AUTHORITY](docs/ADR_CONTINUITY_AUTHORITY.md) |
| Campaign scope, migration, inspection | [GUIDE_CAMPAIGN_CONTINUITY](docs/GUIDE_CAMPAIGN_CONTINUITY.md) |
| Camera lock and bounded recovery | [ADR_CAMERA_VIEW](docs/ADR_CAMERA_VIEW.md) |
