# Kenshi Agent Environment Architecture

This document describes the architecture that is implemented now. The staged
reconstruction plan remains the authority for unfinished reconstruction work;
this file is the compact operating map for the surviving system.

## Purpose and boundaries

The project runs a bounded agent against Kenshi through a small planner-visible
affordance language. A run may use live Kenshi, a deterministic mock world, or
recorded replay evidence. Those environments differ only at their external
mechanics and observation boundaries. They share planning, operation binding,
authorization, scheduling, execution lifecycle, outcome recording, continuity,
supervision, and finalization.

Four guarantees shape the design:

1. The planner chooses only declared affordances. It does not author primitive
   keyboard sequences, native commands, coordinates hidden from observation,
   or controller retry procedures.
2. Every selected operation is bound to exact current evidence, authorized
   against a world revision, and revalidated at the live input boundary.
3. One owner records one terminal result. Acceptance, progress, completion,
   cancellation, and failure remain distinct evidence states.
4. Cleanup is causal. A run is not safely finished merely because a pause key
   was sent; fresh telemetry must confirm pause, and owned interfaces must be
   accounted for.

The native plug-in is an adapter, not an alternate game API. It exports
player-readable state and accepts a fixed set of exact player-order bridges.
The planner cannot call it directly.

## Dependency direction

Dependencies point inward. The outer command and tooling adapters may import
the application, but production application/runtime modules do not import the
tooling perimeter.

```text
Development and console adapters  tooling/*, cli.py, __main__.py
                |                              |
                +-------------> application.py
                                      |
                                 runtime.py
                                      |
          planning and services -> RunCoordinator
                                      |
                       OperationExecutionService
                                      |
                             ExecutionKernel
                           /        |         \
             operation definitions |      handler families
                           \        |         /
                      environment mechanics ports
                         /          |          \
                       live        mock       replay

core/* contains dependency-leaf vocabularies used by every layer.
tooling/* is never imported by the inward production graph.
```

`core/` contains bounded data vocabularies: operations, planning envelopes,
affordances, observations and telemetry, world revisions, authority decisions,
evidence, continuity, scenario proof, planner context, and transport records.
It has no outward project dependencies and its package initializer exports no
convenience barrel.

`application.py` is the public composition root. It owns the public command
parser, config loading and run overrides, environment/planner construction,
the continuity-store lifetime, and creation of `AgentRuntime`. `cli.py` is a
thin console adapter. The supported live-development launcher injects its
verified scenario-attestation loader when it enters the same application root;
the application itself does not know scenario fixture storage.

`runtime.py` composes the run-scoped services. `AgentRuntime` does not contain a
run loop. It wires one coordinator, one operation authority, one action-budget
ledger, one operation-execution factory, planner/advisor/context services, one
continuity service, one outcome recorder, and one event recorder.

## Run lifecycle

`RunCoordinator` is the one physical sequencing owner. Single-step and
continuous execution are scheduling policies inside the same state machine,
not separate runtimes. In the ordinary cycle it:

1. obtains and publishes an observation;
2. assembles planner context from current evidence and bounded continuity;
3. requests and validates a plan;
4. submits each selected affordance to the operation service;
5. records its result and publishes the resulting world revision; and
6. repeats or enters finalization.

The coordinator also joins cross-cutting events that must affect sequencing:
human handoff, supervisor preemption, reflex requests, planning failures,
budget exhaustion, cancellation, and final safe state. It does not branch on
operation families or implement their mechanics.

The coordinator is intentionally larger than a typical service because it is
the explicit state machine that replaced two duplicated run loops and their
scattered failure/finalization routes. Its operation-free loop and import
boundaries are fitness-tested; splitting it is appropriate only when a new
piece has an independent lifecycle owner, not to hide the state machine across
helper objects.

## Planner language and operation lifecycle

`operation_definitions.py` is the private registry connecting the public
affordance language to exact runtime behavior. Each definition declares its:

- operation kind and planner summary;
- control modes, capabilities, risk, and primitive bound;
- binding function and stable handler key;
- reference and selection requirements;
- idempotency and completion owner; and
- optional typed terminal/effect derivation.

`affordances.py` enumerates what the current observation can honestly offer and
binds a selection through the single binding authority. Hosted planners receive
the generated affordance schema plus declared cognitive side operations. There
is no generic skill or configured macro escape hatch.

`OperationExecutionService` binds a plan step and prepares monitoring.
`ExecutionKernel` owns the operation lifecycle:

1. authorize the exact bound operation;
2. reserve global and plan-local budgets;
3. create command identity, execution scope, and an input-boundary token;
4. resolve the definition's one handler;
5. accept handler progress and its typed terminal result;
6. validate the declared completion contract; and
7. commit or release reservations and publish one result.

Handler modules are grouped by cohesive operation family: runtime control,
screens, movement, dialogue, trade, inventory, resources, camera, and cognition.
Composite operations remain one operation. Their deterministic private phases
do not re-enter the planner or scheduler.

## Authority, safety, and evidence

`OperationAuthority` owns cross-cutting per-operation authorization. It uses
the same policy and bound-operation fingerprint before scheduling and again
inside the live input lease with a fresh observation. Operation definitions own
operation-specific prerequisites. The input controller owns host delivery and
does not recreate semantic policy.

The following owners remain deliberately independent because they answer
different questions:

- `SafetySupervisor` watches telemetry, emergency-stop, and control anomalies
  and may preempt an active operation.
- `ControlOwnershipMachine` owns human handoff and the optional visible
  automatic-takeover countdown.
- `ReflexEngine` may propose a bounded urgent operation from current evidence.
- `FinalSafeStateOwner` owns cleanup and confirmed final pause.
- `ActionBudgetLedger` owns mutable rate, purchase, and primitive reservations;
  `OperationPolicy` computes policy decisions without hiding a second ledger.

`WorldStateStore` is the ordered observation and command-revision authority.
Receipts carry command identity, the revision on which execution began, later
evidence, primitive counts, and typed semantic evidence where required.
`OutcomeRecorder` is the one run-level outcome persistence owner. Continuity is
updated from those results rather than inferred from planner prose.

## Environment adapters

`AgentEnvironment` is intentionally narrow: reset, observe, optional fresh
input-boundary observation, and close. It has no semantic `step()` dispatcher.
Each environment exposes the operation-mechanics port consumed by the handler
families.

- `LiveEnvironment` reads plug-in telemetry, captures frames, and exposes
  `KenshiOperationMechanics`. Family-specific mechanics use one
  `KenshiControlSurface` for input lease, calibration, primitive delivery,
  native command transport, acknowledgements, and receipt envelopes. The
  surface contains no operation registry lookup or semantic routing.
- `MockEnvironment` implements the same exact mechanics port over a
  deterministic in-memory world. It tests shared coordination and operation
  contracts without pretending Windows gestures occurred.
- `ReplayEnvironment` advances recorded observations and returns explicit
  non-executing receipts. It is an evidence adapter, not a second scheduler.

The native C++ plug-in and its protocol remain stable behind the live adapter.
Native setup, build, installation, hash, and protocol details live in
`native/KenshiAgentTelemetry/README.md`.
The optional `CalibrationIdentity.macro_set_hash` field remains only because it
is part of that external protocol. It is parsed as observed transport evidence;
there is no configured macro registry or macro execution path in Python.

## Configuration and persistence

`config.py` defines strict Pydantic configuration: unknown fields fail loading.
`config/live.yaml` is the canonical live policy and contains behavior-bearing
knobs only. Live execution still requires the policy setting and the explicit
public command acknowledgements; configuration alone cannot silently enable
host input.

Run evidence is append-only JSONL under the configured runs directory. Durable
memory and fieldbook state are campaign-scoped in SQLite. Scenario claims are
separate from fixture storage: core models describe scenario identity and proof,
runtime validation checks the proof against current telemetry, and tooling owns
capture, restore, and attestation files. A manual label is not promoted into a
verified scenario claim.

Generated schemas and reports come from the current registries and models.
Files under `docs/generated/` and `schemas/` are outputs, not design authorities.

## Tooling perimeter and supported entrypoints

The public agent commands are owned by `application.py`: `run`, `doctor`,
`memory`, `compact-memory`, `fieldbook`, and `validate-telemetry`.

The `tooling/` package owns development-only orchestration: the `./dev` command
contract and live launcher, scenario fixture storage, authored starts, graphics
setup, overlay, evaluations, mutation campaigns, registry audits, and generated
documentation/schema exporters. Scripts at the repository root are thin
tooling entrypoints.

The supported operator workflow is through `./dev doctor`, `./dev launch`,
`./dev run`, `./dev recover`, and `./dev stop`. The generated complete command
reference is `docs/generated/DEV_CLI.md`.

## Change rules

When extending the implemented architecture:

1. Add planner-visible behavior only as a typed operation and definition.
2. Give the definition one handler key and implement mechanics in one family.
3. Keep external delivery behind the environment's mechanics port.
4. Add operation prerequisites to the definition and cross-cutting policy to
   `OperationAuthority`; do not grow a name-based parallel guard.
5. Preserve causal revision, exact binding, terminal ownership, and final-state
   evidence.
6. Keep mock and replay on the same coordinator/kernel path.
7. Put developer orchestration and generated-output logic in `tooling/`.
8. Regenerate schemas and documents from their owners; do not hand-edit them.

Fitness tests enforce the import graph, acyclicity, tooling direction,
single-owner registries, deleted compatibility owners, environment and handler
size limits, command/document parity, and generated-output freshness.
