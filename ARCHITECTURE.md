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
  ├─ planner (heuristic, scripted replay, or hosted vision LLM)
  │     └─ hosted intent proposal ──> deterministic plan compiler
  ├─ strategic advisor (read-only hosted model + attributed guide corpus)
  │     └─ planner context only; no environment or controller authority
  ├─ schema + policy + rate-limit guard
  ├─ skill/macro compiler
  └─ executor
       ├─ interface_only ──> Windows SendInput ──> ordinary Kenshi UI
       └─ native_assisted ──> marked bounded bridge skills + Windows input

Every boundary ──> JSONL session log ──> lifecycle analysis and evaluation
```

Full replay is a separate logging level. Compact digests bound live logs;
`ReplayEnvironment` requires `runtime.log_full_observations: true`.

## Environment contract

`reset()` establishes an episode. `observe()` is side-effect free and requests a
frame when capture exists; `observe_without_capture()` does not. `dispatch(action,
command=...)` supplies a unique command ID and complete revision, and its receipt
binds to the later observation. `step(action)` is the legacy primitive.
`close()` releases resources without manipulating the game; final-state ownership
verifies pause across every exit before environment close.

## Action hierarchy

Raw keys, hotkeys, cursor moves, clicks, and scrolls are controller primitives,
and the generic live planner cannot author them. Run control is separately typed
as noop, wait, pause/speed, and whole-run stop. Everything else is a reusable
semantic action binding current telemetry references through one catalog — the
[generated action catalog](docs/generated/ACTION_CATALOG.md) is the authoritative
list.

Each `ActionContract` owns planner visibility, capability and control-mode
requirements, pointer class, native requirement, risk cost, idempotency,
reference binding, execution route, receipt kind, and completion authority. The
planner composes semantic actions; for hosted idle planning the runtime derives
the bounded envelope, causal fences, graph, retry policy, and budgets from those
choices. It never asks the model to micromanage primitive timing or coordinates.
Legacy skills still expand into bounded primitives for compatibility and
calibrated transport.

Completion is controller-terminal, runtime-derived at the immediate dispatch
baseline, or planner-authored only for genuinely ambiguous effects. The
[completion authority ADR](docs/ADR_ACTION_COMPLETION_AUTHORITY.md) fixes that
ownership boundary; the generated catalog reports it per contract. An action
whose terminal is already satisfied does not bind, and declared failure states
must remain definitively false through the final input lease; see
[non-progress action boundaries](docs/ADR_NON_PROGRESS_ACTION_BOUNDARIES.md).

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
| Action completion ownership | [ADR_ACTION_COMPLETION_AUTHORITY](docs/ADR_ACTION_COMPLETION_AUTHORITY.md) |
| Already-satisfied actions and failure preflight | [ADR_NON_PROGRESS_ACTION_BOUNDARIES](docs/ADR_NON_PROGRESS_ACTION_BOUNDARIES.md) |
| Revision ownership and causal confirmation | [ADR_WORLD_STATE_STREAM](docs/ADR_WORLD_STATE_STREAM.md) |
| Final in-lease authorization fence | [ADR_INPUT_BOUNDARY_AUTHORITY_V2](docs/ADR_INPUT_BOUNDARY_AUTHORITY_V2.md) |
| Transactional global and plan budgets | [ADR_TRANSACTIONAL_ACTION_BUDGETS](docs/ADR_TRANSACTIONAL_ACTION_BUDGETS.md) |
| Evidence strength and cross-layer consistency | [ADR_EVIDENCE_VOCABULARY_V2](docs/ADR_EVIDENCE_VOCABULARY_V2.md) |
| Pointer calibration identity | [ADR_CALIBRATION_IDENTITY](docs/ADR_CALIBRATION_IDENTITY.md) |
| Movement options and active-step interruption | [ADR_ACTIVE_OPTION_INTERRUPTION](docs/ADR_ACTIVE_OPTION_INTERRUPTION.md) |
| Independent preemption and safe pause | [ADR_SAFETY_SUPERVISOR](docs/ADR_SAFETY_SUPERVISOR.md) |
| Native handle identity and lifecycle | [ADR_STABLE_NATIVE_IDENTITY](docs/ADR_STABLE_NATIVE_IDENTITY.md) |
| Native request/acknowledgement causality | [ADR_CAUSAL_NATIVE_COMMANDS](docs/ADR_CAUSAL_NATIVE_COMMANDS.md) |
| How far the plug-in may go | [ADR_NATIVE_INTEGRATION_SCOPE](docs/ADR_NATIVE_INTEGRATION_SCOPE.md) |
| Read-only advisor boundary | [ADR_STRATEGIC_ADVISOR](docs/ADR_STRATEGIC_ADVISOR.md) |
| Hosted context capacity and proactive projection | [ADR_HOSTED_CONTEXT_CAPACITY](docs/ADR_HOSTED_CONTEXT_CAPACITY.md) |
| Hosted intent and deterministic plan compilation | [ADR_HOSTED_PLAN_PROPOSALS](docs/ADR_HOSTED_PLAN_PROPOSALS.md) |
| Action-outcome continuity between calls | [ADR_ACTION_LEDGER](docs/ADR_ACTION_LEDGER.md) |
| Exact entity memory retrieval | [ADR_ENTITY_SCOPED_MEMORY](docs/ADR_ENTITY_SCOPED_MEMORY.md) |
| Continuity authority and commit timing | [ADR_CONTINUITY_AUTHORITY](docs/ADR_CONTINUITY_AUTHORITY.md) |
| Continuity store failure isolation and planner feedback | [ADR_CONTINUITY_FAILURE_ISOLATION](docs/ADR_CONTINUITY_FAILURE_ISOLATION.md) |
| Campaign scope, migration, inspection | [GUIDE_CAMPAIGN_CONTINUITY](docs/GUIDE_CAMPAIGN_CONTINUITY.md) |
| Camera lock and bounded recovery | [ADR_CAMERA_VIEW](docs/ADR_CAMERA_VIEW.md) |
