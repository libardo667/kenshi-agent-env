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
  │                                             ├─ runtime affordance adapters
  │                                             ├─ private operation contracts
  │                                             └─ monitored/composite options
  │                                                   ↕ future/change-course patch
  ├─ reflex layer (shared deterministic pause/stop rules)
  ├─ planner (heuristic, scripted replay, or hosted vision LLM)
  │     └─ exact affordance selections ──> deterministic plan compiler
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

## Affordance lifecycle

The playing model has one action language: current `AffordanceOffer` instances.
It selects an offer ID, its exact offered target, and only declared gameplay
parameters. Runtime adapters generate those offers from named bindings, visible
controls, contextual orders, dialogue, inventory, characters, map destinations,
and native or composite operations. The [generated affordance
catalog](docs/generated/AFFORDANCE_CATALOG.md) derives directly from that adapter
registry and records each source-specific completeness boundary.

After selection, the runtime re-enumerates the same source and binds the exact
offer before materializing a private typed operation. `ActionContract` now owns
only that operation's deterministic mechanics: capability and control-mode
fences, reference binding, risk, idempotency, execution route, and completion.
Raw keys, pointer motion, playback transitions, monitoring, retries, and cleanup
never enter the hosted schema. Calibrated skills remain private transport.

Every hosted selection retains its affordance provenance through execution and
closes with the common offered, bound, executing, optional monitoring, and
terminal receipt vocabulary. Source-specific evidence remains nested below
that lifecycle. See [the unified affordance contract
decision](docs/ADR_UNIFIED_AFFORDANCE_CONTRACT.md).

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
| Unified planner action language and lifecycle | [ADR_UNIFIED_AFFORDANCE_CONTRACT](docs/ADR_UNIFIED_AFFORDANCE_CONTRACT.md) |
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
