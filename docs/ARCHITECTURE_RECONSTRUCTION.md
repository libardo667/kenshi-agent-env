# Kenshi Agent Environment: Staged Architectural Reconstruction

**Status:** Complete and closed. Historical context only.
**Historically succeeded by:** `docs/archive/KENSHI_INTERACTION_SCOPE_ORDER_LIFECYCLE_PLAN.md`, which is
the active authority. All eight stages here are accepted at tag
`reconstruction-stage-8-accepted`; §20.4 forbids reopening them. Read this
document to understand why the current architecture exists, not to decide what
to build next.
**Applies to:** `libardo667/kenshi-agent-env` after `56b9e0d8`  
**Precedent:** `6ba46c9` and `3f7cce6`  
**Purpose:** Demolish the accumulated internal architecture without losing the proven playing system on the other side.

---

## 1. Mandate

This is not a cleanup pass, a gradual extraction exercise, or a request to make the existing architecture more elegant one helper at a time.

It is a staged reconstruction.

The repository has accumulated several individually defensible systems that now overlap in authority. Affordance adapters, action contracts, plan validation, the continuous executor, the live environment, the runtime, the input boundary, and safety machinery all know some portion of the same answer: what an action means, whether it is allowed, how it runs, what proves it worked, and what should happen when it does not.

The goal is to reduce that architecture to a small number of explicit owners while preserving the externally proven behavior.

The working posture is:

- Prefer replacing a subsystem over wrapping it indefinitely.
- Prefer deleting a competing representation over synchronizing two representations.
- Prefer one large coherent change over a trail of micro-adjustments that preserve every historical seam.
- Permit temporary breakage inside a reconstruction stage.
- Require each stage to end green, runnable, and with fewer authorities than it began.
- Do not advance gameplay capability during reconstruction.

Historical ADRs, tests, logs, and live evidence explain why the present code exists. They are not commands to preserve its present shape. Where this plan explicitly supersedes an older internal boundary, follow this plan.

“Fail closed” remains important at the real external boundaries: stale telemetry, ambiguous identity, human control, unowned input, native command causality, and final safe state. It is not a reason to preserve redundant Python layers, obsolete APIs, duplicate validation, or compatibility configuration.

Kenshi saves used by this project are disposable and live work is supervised. Purchases, combat, inventory changes, and save changes are ordinary gameplay operations, not high-hazard product actions. Host input ownership and operator handoff deserve exactness; ordinary in-game consequences do not justify architectural paralysis.

---

## 2. Why the two clean commits worked

Commits `6ba46c9` and `3f7cce6` succeeded because they did not cautiously reconcile the old planner action union with a new affordance system.

They made a clean decision:

> The playing model selects one exact current affordance. Runtime code owns the mechanics.

That decision enabled several deletions at once:

- The planner stopped authoring internal action mechanics.
- Source-specific possibilities became one `AffordanceOffer` language.
- Selection was rebound against the current source before execution.
- The old generated action catalog was replaced by an affordance catalog.
- Large portions of planner schema translation and planner tests disappeared.
- Compatibility with the old planner-visible action union was explicitly rejected.

The important pattern was not “add an affordance module.” The pattern was:

1. Choose one owner.
2. State the new boundary in one sentence.
3. Route every supported case through it.
4. Delete the rival language.
5. Generate audits from the surviving authority.
6. Prove behavior at the boundary rather than preserving implementation history.

Every stage below must follow that pattern.

---

## 3. Current architectural knot

The planner boundary is now substantially cleaner, but the private runtime beneath it still forms an execution sandwich.

A selected affordance currently travels through overlapping layers:

1. `affordances.py` enumerates and rebinds the offer, then materializes a private `Action`.
2. `action_contracts.py` binds references and owns capabilities, control modes, risk, idempotency, execution classification, primitive bounds, and completion ownership.
3. `planning.py` validates plans and completion conditions.
4. `continuous_executor.py` branches on action type, runs atomic actions, monitored options, composite operations, memory reads, advisor calls, harvest transactions, retries, and patches.
5. `env/live.py` branches on action type again and owns observation, input leasing, native commands, UI mechanics, bounded trade, camera recovery, resource transfer, and semantic completion details.
6. `safety.py` and `input_boundary.py` repeat portions of action authorization at different times.
7. `runtime.py` owns two schedulers, planner calls, continuity, fieldbook, memory, advisor integration, safety preemption, outcome assessment, logging, and finalization.

The scale reflects the overlap:

- `LiveEnvironment`: roughly 4,300 lines.
- `AgentRuntime`: roughly 3,900 lines.
- `ContinuousPlanExecutor`: roughly 3,800 lines.
- `ContinuousPlanExecutor._execute_step`: roughly 1,200 lines.
- `models.py`: roughly 5,400 lines and imported across most of the package.
- `action_contracts.py`: roughly 3,600 lines.

This is not mainly a file-size problem. Splitting those files without changing ownership would distribute the knot across more files.

The principal duplications to demolish are:

- Affordance binding versus action-contract binding.
- Action contracts versus executor routing versus environment routing.
- Runtime conditions versus controller terminals versus option terminals spread across several modules.
- Single-step and continuous run loops.
- Plan-time authorization versus dispatch-time authorization versus environment checks.
- Observation, planner context, memory, fieldbook, advisor, and outcome assembly inside one runtime class.
- A universal `models.py` vocabulary that creates package-wide coupling and import cycles.

---

## 4. Product surface to preserve

Only supported behavior and evidence are protected. Internal APIs are not.

The reconstruction must preserve:

- The playing-model language: exact current `AffordanceSelection` against runtime-generated offers.
- The canonical supervised live path in `config/live.yaml`.
- `./dev launch`, `./dev run`, `./dev recover`, and `./dev stop` behavior.
- Current native telemetry and command protocol compatibility unless a stage explicitly versions it.
- One authoritative observation stream and monotonically ordered world revisions.
- Exact identity and current-source rebinding.
- Human-input handoff and F12 emergency stop.
- Independent supervisor preemption.
- Native command cancellation and causal terminal acknowledgement.
- One final safe-state owner and confirmed terminal pause.
- Current run-bundle readability and evidence semantics. Version a record deliberately if its shape must change; never silently reinterpret old records.
- Campaign-scoped memory, fieldbook, and continuity semantics.
- Mock and replay as test adapters.
- The currently proven live capability set, including movement, dialogue, trade, harvesting, selling, purchasing, interface ownership, and clean close.

The following are not protected merely because they exist:

- `AgentEnvironment.step()`.
- Separate implementations of `single_step` and `continuous` scheduling.
- `ActionContract`, `ReferenceBinding`, or the `ACTION_CONTRACTS` registry.
- Current module names or class names.
- Compatibility fields that the current runtime does not read.
- Planner or runtime flags with no supported live role.
- Old generated documents whose authority is superseded.
- Internal `Action` classes as a package-wide public vocabulary.
- Tests that pin helper names, branch structure, or old module placement rather than behavior.

`single_step` may remain as a user-facing scheduling policy for mock and deterministic tests. It may not retain a separate runtime implementation.

---

## 5. Target ownership model

The reconstructed system should be explainable with the following ownership table.

| Question | Sole owner |
| --- | --- |
| What can the playing model choose now? | Affordance adapters |
| How is an exact selection rebound? | The adapter that issued the offer |
| What private operation results? | Adapter binder |
| What domain prerequisites, risk, and terminal belong to that operation? | Operation definition |
| How is the operation mechanically performed? | Operation handler |
| Is host input authorized at this exact moment? | Operation authority / input lease |
| What independently preempts the run? | Safety supervisor |
| Who owns human handoff? | Control-ownership state machine |
| Who guarantees terminal pause and cleanup? | Final-state owner |
| Who schedules observe/plan/execute/repeat? | Run coordinator |
| Who builds the planner payload? | Planner-context assembler |
| Who records outcomes and continuity evidence? | Outcome recorder and continuity service |
| Who reads Kenshi state? | Kenshi observation adapter |
| Who sends UI or native mechanics? | Controller and native transport adapters |

No responsibility may have two owners at final completion.

### Target dependency direction

```text
core types
  ↑
application services
  ↑
operation definitions and handlers
  ↑
Kenshi / mock / replay / planner / storage adapters
  ↑
tooling and CLI
```

Core types must not import application services, adapters, the runtime, or tooling. Operation handlers may depend on narrow ports, never on a giant live environment. Tooling may depend inward; production code must not depend on tooling.

### Suggested package shape

The exact names may change, but the responsibilities should resemble:

```text
kenshi_agent/
  core/
    world.py
    affordance.py
    operation.py
    planning.py
    evidence.py
    continuity.py
    transport.py

  application/
    run_coordinator.py
    planner_service.py
    planner_context.py
    execution_kernel.py
    operation_authority.py
    outcome_recorder.py
    finalizer.py

  operations/
    runtime.py
    screens.py
    bindings.py
    movement.py
    dialogue.py
    inventory.py
    trade.py
    resources.py
    camera.py
    cognition.py

  ports/
    observation.py
    input.py
    native.py
    capture.py
    planner.py
    stores.py

  adapters/
    kenshi/
      observation.py
      input.py
      native.py
      capture.py
      quicksave.py
    mock/
    replay/
    planners/
    sqlite/

  tooling/
    cli.py
    live_dev.py
    docs.py
    evals/
```

Do not create empty layers or generic frameworks merely to match this diagram. A package earns existence only when code has moved into it and an old authority has been deleted.

---

## 6. Reconstruction laws

These rules apply to every stage.

### 6.1 One authority per concept

A stage is not complete if old and new implementations both remain available for the same operation or decision.

A temporary migration fallback is permitted only when:

- It owns only operations not yet migrated.
- No operation can take both paths.
- It is named as temporary in the stage plan.
- Its deletion is a hard exit criterion for that stage or the immediately following stage.
- It receives no new features or fixes except what is necessary to complete migration.

### 6.2 Large coherent edits are allowed

Inside a reconstruction branch, temporary failing tests and incomplete imports are acceptable. Codex should not stop after every helper extraction to restore the entire suite.

During a stage:

- Use targeted tests while moving a subsystem.
- Complete the whole ownership change.
- Delete the old path.
- Then run the full stage gate.

Do not make dozens of tiny commits whose only purpose is to keep every intermediate state releasable. Prefer one to three meaningful commits per stage:

1. Characterize and establish the boundary.
2. Replace and delete.
3. Prove and update generated artifacts.

### 6.3 Preserve outcomes, not call graphs

Tests should pin:

- Offer enumeration and exact rebinding.
- Operation authorization.
- Primitive/native request issuance.
- Causal terminal evidence.
- Cancellation and cleanup.
- Outcome and continuity records.

Tests should not require an old helper to be called, preserve a former class hierarchy, or assert that logic remains in a named module.

### 6.4 No frontier work

Until reconstruction is complete, do not add:

- New gameplay affordances.
- New native protocol fields unless required to preserve existing behavior.
- New memory semantics.
- New advisor capabilities.
- New mutation campaigns for untouched frontier work.
- New planner modes.
- New compatibility options.
- New generalized audit frameworks unrelated to the current stage.

Fix a live regression only when it blocks the current stage’s acceptance proof.

### 6.5 Every stage must delete something structural

Moving code is insufficient. Each stage exit report must name:

- The former authority removed.
- Files, classes, methods, branches, flags, or schemas deleted.
- The surviving owner.
- The evidence that the surviving path works.

### 6.6 Full gates happen at stage end

The usual portable gates remain:

```text
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python scripts/export_schemas.py
uv run python scripts/export_docs.py
```

Run native build/protocol gates only in stages that touch the native boundary or at final acceptance. Run supervised live proofs only where the stage matrix below requires them.

---

## 7. Stage 0 — Freeze, baseline, and reconstruction authority

### Thesis

Stop treating the accumulated code and old prompts as the active design authority. Establish a known-good baseline and one reconstruction document before demolition begins.

### Work

1. Create a reconstruction branch from current `main`, for example:

   ```text
   architecture-reconstruction
   ```

2. Tag the current known-good point, for example:

   ```text
   pre-architecture-reconstruction-2026-08-03
   ```

3. Commit this plan to the repository as `docs/ARCHITECTURE_RECONSTRUCTION.md`.

4. Add a brief notice to old loop prompts and architectural steering documents:

   ```text
   Historical context only during reconstruction. docs/ARCHITECTURE_RECONSTRUCTION.md is authoritative.
   ```

   Do not rewrite every ADR now.

5. Record a baseline manifest containing:

   - Current commit.
   - Python, dependency, and native build environment.
   - Full portable gate results.
   - Generated schema/doc hashes.
   - Current native DLL/protocol hashes.
   - Current public CLI commands.
   - Current canonical live configuration hash.
   - Current memory schema version.

6. Preserve representative evidence bundles or distilled deterministic traces for:

   - Affordance enumeration and rebinding.
   - One native movement terminal.
   - One ordinary UI transaction.
   - The harvest/sell/buy economic loop.
   - Human-input handoff and clean final pause.
   - Restart continuity portable evaluation.

   Existing live bundles may be used. Do not rerun every proof merely to create a newer timestamp.

7. Add architecture fitness ratchets that prohibit further growth in the principal demolition targets. At minimum, new changes must not increase line count or semantic dispatch branches in:

   - `models.py`
   - `action_contracts.py`
   - `continuous_executor.py`
   - `env/live.py`
   - `runtime.py`

   These are temporary demolition ratchets, not permanent style law.

### Deletions

None required beyond retiring obsolete active prompts. This stage establishes the controlled starting point.

### Exit criteria

- Reconstruction branch and baseline tag exist.
- This plan is the explicit authority.
- Full portable gates pass.
- Baseline artifacts are recorded.
- No frontier work is queued in the reconstruction prompt.

### Live proof

None required if current bundles and the current HEAD already establish the baseline.

---

## 8. Stage 1 — Collapse private operation contract authority

### Thesis

An affordance should bind directly to one private operation definition. The runtime should not materialize an action and then consult a second giant contract system to rediscover its meaning.

### Current authority to remove

- `ActionContract`
- `ReferenceBinding`
- `ACTION_CONTRACTS`
- `contract_for()`
- `completion_contract_for()` as a universal second lookup
- Stringly `operation_kind` followed by later reconstruction of the operation contract

### Target

Introduce one `OperationDefinition` per private operation. It owns:

- Private operation type.
- Domain prerequisites.
- Current authorability.
- Exact binding.
- Risk and transactional budget cost.
- Idempotency.
- Primitive bound.
- Control-mode and capability requirements.
- Terminal authority.
- Handler identity.

The affordance adapter that issued an offer must re-enumerate the current source and bind the selection directly into a `BoundOperation` using the corresponding operation definition.

A useful conceptual shape is:

```python
@dataclass(frozen=True)
class OperationDefinition(Generic[OperationT, BindingT]):
    kind: str
    operation_type: type[OperationT]
    bind: Callable[[OperationT, Observation], BindingT]
    policy: OperationPolicy
    terminal: TerminalDefinition
    handler_key: str

@dataclass(frozen=True)
class BoundOperation(Generic[OperationT, BindingT]):
    definition: OperationDefinition[OperationT, BindingT]
    operation: OperationT
    binding: BindingT
    affordance: BoundAffordance
    based_on_revision: WorldStateRevision
```

Do not preserve one universal `ReferenceBinding` containing optional fields for every operation family. Each family gets a narrow typed binding.

Examples:

- `BoundTradeCell`
- `BoundWorldTarget`
- `BoundCharacterMovement`
- `BoundResourceHarvest`
- `BoundVisibleControl`
- `BoundMapDestination`

Run-control and cognitive operations may have empty or purpose-specific bindings.

### Migration cohorts

Migrate definitions in families so the repository can remain comprehensible:

1. Runtime/cognitive: noop, stop, advisor, memory, fieldbook.
2. Screens/bindings/visible controls.
3. Characters/dialogue/movement.
4. Inventory/trade/equipment.
5. Resources/camera/composite operations.

A temporary `LegacyMechanics` executor may remain after this stage because Stage 2 moves mechanics. It may execute an already-bound operation, but it may not own binding, policy, risk, capabilities, or terminal selection.

### Generated evidence

Replace the private operation queue with a registry-derived report that proves:

- Every adapter-emitted operation has exactly one definition.
- Every definition has exactly one handler key.
- No private operation is unbound or multiply defined.
- Completeness boundaries remain source-specific.

### Required deletions

At stage exit:

- Delete `src/kenshi_agent/action_contracts.py`.
- Delete contract-specific tests that merely restate the old registry.
- Delete `ActionExecution` and `CompletionOwner` if the new operation vocabulary supersedes them; otherwise move their narrowed equivalents into core operation types.
- Delete `ReferenceBinding`.
- Delete generated artifacts that derive from the old contract registry.
- Remove all imports of `contract_for` and `completion_contract_for`.

### Exit criteria

- There is one private operation-definition registry.
- Affordance rebinding produces a typed `BoundOperation` directly.
- No planner-visible schema exposes private mechanics.
- All 25 current operations have exactly one definition.
- No old action-contract path remains.
- Portable gates pass.
- Existing offer and terminal traces remain semantically equivalent.

### Live proof

No new live run is required. This stage changes private definition and binding authority, not mechanics. Replay/golden traces must prove the same exact selections bind or fail closed.

---

## 9. Stage 2 — Collapse execution routing and demolish the semantic environment

### Thesis

A private operation should be executed by exactly one operation handler. It should not pass through a scheduler action switch and then a second live-environment action switch.

### Current authorities to remove

- `ContinuousPlanExecutor._execute_step()`.
- `ContinuousPlanExecutor._execute_resource_harvest()` as an exceptional hard-coded transaction.
- `ContinuousPlanExecutor._execute_monitored_option()` as a central type switch.
- Semantic action routing in `LiveEnvironment._execute_live()`.
- Semantic implementations embedded in `LiveEnvironment`, including bounded trade, camera recovery, approach mechanics, and resource transfer.
- `AgentEnvironment.step()` and its legacy dispatch seam.

### Target

Create an `ExecutionKernel` that accepts one already-bound operation and resolves exactly one handler from the operation definition.

The kernel owns only cross-cutting lifecycle:

1. Revalidate current authority.
2. Reserve transactional budgets.
3. Record `offered` and `bound` lifecycle state.
4. Invoke the handler.
5. Accept handler monitoring/progress events.
6. Record one terminal result.
7. Release or commit budget reservations.
8. Route cancellation and cleanup.

Operation handlers own mechanics and operation-specific monitoring.

A useful protocol is:

```python
class OperationHandler(Protocol[OperationT, BindingT]):
    async def execute(
        self,
        bound: BoundOperation[OperationT, BindingT],
        context: OperationContext,
    ) -> OperationResult: ...

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult: ...
```

`OperationContext` supplies narrow ports:

- Current world reader/subscription.
- Input lease and primitive controller.
- Native command transport.
- Capture service.
- Run logger.
- Clock.
- Cancellation signal.

It must not expose `AgentRuntime` or a giant `LiveEnvironment`.

### Handler families

Create cohesive handler modules rather than one file per trivial operation:

- `runtime.py`: noop and stop.
- `cognition.py`: advisor, memory, fieldbook.
- `screens.py`: open, dismiss, activate, scroll, game bindings.
- `movement.py`: move to character, move in direction, travel, regroup, exit building, threat response.
- `dialogue.py`: approach and dialogue activation mechanics.
- `trade.py`: purchase and sale.
- `inventory.py`: equip and inventory-window ownership.
- `resources.py`: produce, open resource inventory, collect output, bounded harvest transaction.
- `camera.py`: rotation and recovery.

A composite operation remains one operation. Its handler may perform several deterministic substeps, but those substeps are private mechanics and do not re-enter the planner or scheduler.

### Live adapter after demolition

The Kenshi adapter should own only external mechanics:

- Read telemetry.
- Request capture.
- Acquire and validate a host-input lease.
- Send primitive input.
- Send/cancel native commands.
- Inspect quicksave filesystem state.
- Close resources without deciding semantic gameplay outcomes.

It must not import semantic operation classes or branch on them.

Mock and replay should implement the same operation execution boundary through their own adapters. Do not force mock gameplay through Windows primitives.

### Migration strategy

Migrate the same five operation families used in Stage 1. During migration, a single temporary fallback may own only unmigrated handler keys. An operation must never be executable by both old and new handlers.

The stage is not complete until the fallback is empty and deleted.

### Required deletions

At stage exit:

- Delete `_execute_step`.
- Delete central action-type switches from the scheduler/executor.
- Delete semantic action switches from the live environment.
- Delete `AgentEnvironment.step()`.
- Delete `LegacyMechanics` fallback.
- Delete option classes that exist only to compensate for central execution routing; retain only reusable monitors that have a clear independent role.
- Delete duplicate terminal derivation from planning and environment modules.

### Size and dependency gates

At stage exit:

- No environment class may exceed 1,500 lines without an explicit external-adapter justification.
- No operation dispatcher may use a growing `isinstance` chain.
- No operation handler method should exceed roughly 250 lines without being split into typed phases.
- `ExecutionKernel` should be orchestration, not operation knowledge.
- Every operation definition maps to exactly one handler.

These are reconstruction gates, not invitations to hide complexity in nested functions.


**Residue:** this stage's exit criteria are met except for the adapter-boundary leak in Section 20.1. Do not reopen Stage 2 to fix it; close it with Stage 4.
### Exit criteria

- One operation execution registry.
- One handler per operation.
- The live environment is a collection of external ports/adapters, not a semantic game engine.
- Atomic, monitored, and composite behavior share one lifecycle.
- Controller-owned composite actions count as semantic transactions for global rate accounting; internal verified primitives retain their per-operation bound but do not masquerade as dozens of strategic actions.
- Portable gates pass.
- Native protocol/build gates pass if native adapter code changed.

### Live proof

Run one supervised vertical proof that exercises both UI and native mechanics. Preferred proof:

1. Start from a broke character or pair.
2. Harvest a bounded resource output.
3. Sell it.
4. Buy food.
5. End in a confirmed clean pause.

This is the best proof that the new kernel handles native movement, composite execution, inventory ownership, UI input, conservation terminals, and finalization through one path.

Do not require a long autonomy soak yet.

---

## 10. Stage 3 — Replace the two runtime loops with one coordinator

### Thesis

`single_step` and `continuous` are scheduling policies, not different runtimes.

### Current authorities to remove

- `AgentRuntime._run_single_step()`.
- `AgentRuntime._run_continuous()`.
- Duplicated planner error handling, observation acquisition, guard application, outcome recording, stop handling, and finalization across them.
- Scheduler logic embedded in `AgentRuntime`.

### Target

Create one explicit `RunCoordinator` state machine:

```text
STARTING
  -> OBSERVING
  -> PLANNING
  -> BINDING
  -> EXECUTING
  -> RECORDING
  -> OBSERVING

Any active state
  -> HANDOFF
  -> PREEMPTING
  -> FINALIZING
  -> FINISHED
```

The coordinator owns sequencing only. It delegates:

- Observation to `WorldStateStore` / observation service.
- Planner calls to `PlannerService`.
- Affordance binding to the adapter registry.
- Execution to `ExecutionKernel`.
- Preemption to supervisor/control events.
- Outcome persistence to `OutcomeRecorder`.
- Final safe state to the finalizer.

`single_step` becomes a scheduling policy such as “one planning/execution cycle, then return.” Continuous planning becomes “repeat until termination.” Both use the same state machine and services.

Concurrent future planning should be an optional planning policy/service. It may subscribe to a long-running active operation and stage a future selection, but it must not live inside the core coordinator or operation handler.

### Required deletions

At stage exit:

- Delete `_run_single_step` and `_run_continuous`.
- Delete `ContinuousPlanExecutor` if its remaining responsibilities are now divided between `RunCoordinator`, `ExecutionKernel`, and planning policy.
- Delete mode-specific branches from outcome recording and finalization.
- Delete any configuration flag that selects an implementation rather than a policy.


**Closed 2026-08-03:** loop unification and the physical coordinator extraction
are complete. `AgentRuntime` is a composition root, `RunCoordinator` is the one
run-sequencing owner, and `ContinuousPlanExecutor` is narrowed to plan-local
execution as recorded in Section 20.2.
### Exit criteria

- One run loop.
- One planner-call lifecycle.
- One route for planner errors and malformed selections.
- One route for stop, budget exhaustion, cancellation, exceptions, and finalization.
- Single-step mock tests and continuous live tests exercise the same coordinator.
- The coordinator contains no operation-family logic.
- Portable gates pass.

### Live proof

Run a short supervised continuous session, approximately 10–20 strategic selections, proving:

- Repeated observe/plan/execute cycles.
- At least one long operation.
- Replanning after a state-changing terminal.
- Clean stop and final pause.

The goal is coordinator continuity, not novel gameplay.

---

## 11. Stage 4 — Consolidate action authority without merging independent supervision

### Thesis

Cross-cutting authorization should be computed once and revalidated once at the real input boundary. Independent preemption should remain independent.

### Current overlap to remove

- Capability, control-mode, interface, selection, risk, stale-state, and reference checks repeated among planning validation, operation definitions, `ActionGuard`, input-boundary tokens, executor code, and live environment code.
- Different text representations of the same refusal.
- Revalidation that reconstructs policy differently at plan time and input time.

### Target

Introduce one `OperationAuthority` that evaluates a bound operation against an observation and returns a typed authorization decision.

```python
@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    code: AuthorizationCode
    based_on_revision: WorldStateRevision
    operation_fingerprint: str
    details: Mapping[str, JsonValue]
```

The same policy implementation is used:

- Before an operation is scheduled.
- Immediately before the first host/native primitive inside the input lease.

The second check uses a fresh observation and must return a decision for the same bound-operation fingerprint. It does not rerun a different collection of ad hoc guards.

Operation-specific prerequisites belong to the operation definition. Cross-cutting host/runtime concerns belong to `OperationAuthority`.

Keep these independent:

- `SafetySupervisor`: observes world/control conditions and can preempt any operation.
- `ControlOwnershipMachine`: owns human handoff and automatic takeover countdown.
- `ReflexEngine`: deterministic urgent gameplay response, if still justified as a distinct producer of operation requests.
- `FinalSafeStateOwner`: owns terminal cleanup and confirmed pause.

Do not merge them into a giant “safety manager.” Independence is useful here because these components react to different event streams. The demolition target is duplicate per-operation authorization, not independent observation.

### Required deletions

At stage exit:

- Delete `ActionGuard` if its entire role is replaced; otherwise rename and narrow it to the single authority implementation.
- Delete duplicate input-boundary validation code.
- Delete environment-level semantic permission checks.
- Delete plan validators that restate operation policy rather than validating plan structure.
- Delete duplicate refusal strings in favor of typed codes rendered at the edge.


**Closed 2026-08-04.** Bound-operation identity, one fresh rebinding authority,
structural-only plan policy, pure operation policy, mutable budget accounting,
and the external-adapter leak are closed together. See Sections 20.1 and 20.3.
### Exit criteria

- Every operation has one domain policy owner.
- Every host/native dispatch has one cross-cutting authority owner.
- Plan-time and input-time checks use the same typed policy.
- Supervisor preemption remains independently testable.
- Human input emits zero agent primitives after handoff.
- Interrupted native commands are cancelled and reach a causal terminal.
- Final pause is confirmed through the one finalizer.
- Portable gates pass.

### Live proof

Run a supervised control-boundary proof that includes:

- Begin a monitored/native operation.
- Interrupt with human input and verify visible handoff.
- Allow or decline automatic takeover as configured.
- Exercise F12 or an equivalent test path.
- Recover or stop.
- Confirm native cancellation and final pause.

Do not require gameplay progress in this proof.

---

## 12. Stage 5 — Extract planner context, outcome recording, and continuity services

### Thesis

The run coordinator should not know how memory is ranked, how fieldbook receipts are bounded, how advisor calls are cached, how world deltas are assessed, or how continuity evidence is committed.

### Current authority to remove

The broad integration role currently concentrated in `AgentRuntime`, including:

- Planner payload assembly.
- Memory recall and search.
- Fieldbook reads and writes.
- Advisor task management.
- Continuity receipt windows.
- Outcome assessment.
- Session logging details.
- Planner-delivery recording.

### Target services

#### PlannerContextAssembler

Inputs:

- Current world snapshot.
- Current affordance offers.
- Recent typed operation outcomes.
- Selected memory recall.
- Fieldbook summary/read result.
- Advisor brief availability/result.
- Run objective and scheduling state.

Output:

- One immutable authored planner context.

It is the only place that decides what the playing model sees. Observation models must not lazily import affordance enumeration or memory compaction to build themselves.

#### ContinuityService

Owns:

- Memory and fieldbook operation validation.
- Commit timing.
- Campaign scope.
- Evidence resolution.
- Failure isolation.
- Read receipts and bounded result windows.

Memory and fieldbook remain distinct stores and semantic systems. Do not merge them merely because both appear in planner context.

#### OutcomeRecorder

Owns:

- Comparing the operation baseline to causally later evidence.
- Producing `ActionOutcome` / operation outcome records.
- Decision-relevant versus mechanical delta classification.
- Logging terminal lifecycle records.
- Feeding continuity and evaluation.

Operation handlers provide typed terminal evidence; the recorder should not reverse-engineer mechanics from prose receipts.

#### AdvisorService

Owns read-only strategic calls, cadence, cache/fingerprint, and result delivery. It never gains operation or environment authority.

### Required deletions

At stage exit:

- Remove memory, fieldbook, advisor, and continuity internals from the coordinator.
- Remove outcome-assessment branches from the coordinator.
- Delete observation methods that assemble planner context by importing higher layers.
- Delete duplicate bounded receipt windows outside the continuity service.

### Exit criteria

- Coordinator depends only on service interfaces.
- Planner payload has one producer.
- Outcome records have one producer.
- Continuity operations have one authority.
- Existing restart-continuity evaluation passes unchanged in meaning.
- Memory/fieldbook database history remains readable.
- Portable gates pass.

### Live proof

No live run is required unless integration changes break the canonical live path. Portable restart, replay, and deterministic planner-context tests are the authoritative proof for this stage.

---

## 13. Stage 6 — Split the model universe and remove package cycles

### Thesis

Only after ownership is simplified should the type system be divided. This stage makes the new architecture physically true in imports.

### Current authority to remove

- Universal `models.py`.
- Package-wide imports from a mixed vocabulary containing telemetry, planner messages, actions, receipts, memory, fieldbook, advisor, conditions, and continuity.
- Lazy imports used to break cycles after the fact.
- Runtime/application dependencies hidden inside model methods and validators.

### Target type packages

Move types by stable bounded context:

- `core/world.py`: telemetry-independent world revision and observation types.
- `core/telemetry.py` or Kenshi adapter schema: native telemetry payload types.
- `core/affordance.py`: offer, selection, target, lifecycle provenance.
- `core/operation.py`: private operation request, binding, definition, active state, result.
- `core/planning.py`: planner output and scheduling types.
- `core/evidence.py`: terminal evidence and outcome records.
- `core/continuity.py`: memory/fieldbook evidence references and receipts.
- `core/transport.py`: primitive input/native request receipts where genuinely shared.

Use direct imports. Do not replace `models.py` with a new giant `core/__init__.py` barrel that re-exports everything.

Pydantic schemas should continue to generate from explicit root models. Schema generation is a consumer of core types, not a reason for all types to live in one file.

### Import fitness gates

At stage exit:

- No strongly connected component among production package modules.
- No lazy import from a core model into affordance enumeration, memory compaction, runtime, or handlers.
- Core modules import no application, adapters, CLI, or tooling.
- No umbrella type module becomes the new package-wide dependency hub.
- New imports use the defining module, not a convenience barrel.

### Required deletions

- Delete `src/kenshi_agent/models.py`.
- Delete compatibility re-exports for old import paths before stage exit.
- Delete obsolete type aliases and tagged unions not used by supported roots.
- Delete model validators that perform application work.

### Exit criteria

- `models.py` is gone.
- The package dependency graph is acyclic.
- Schemas regenerate deterministically.
- Planner schema still exposes only affordance selection and declared cognitive side operations.
- Run bundles and stored records remain readable or have an explicit versioned migration.
- Portable gates pass.

**Closed 2026-08-04.** The 5,417-line universal `models.py` and its old import
path are deleted. Types now live in direct bounded-context modules under
`core/` for authority, affordance, telemetry, operations, world revision,
evidence, continuity, advisor vocabulary, planning, observation, authored
planner context, and transport. `core/__init__.py` exports nothing.

Planner payload construction moved from `Observation` to the planner-context
owner; condition evaluation is a direct module used by planning and input
revalidation; context-menu envelope validation, compaction candidate identity,
and authorization verdicts live with their core vocabularies. Function-local
core imports and the environment adapter re-export barrel are gone.

Fitness tests parse the production import graph and require zero strongly
connected components, no outward core dependency, no function-local core
imports, no universal model module, and no core convenience barrel. Repeated
schema generation was byte-for-byte stable with no schema changes. Hosted
planner-contract, replay, restart-continuity, memory, and fieldbook tests
confirmed that planner visibility and stored evidence remain readable without
migration. The full portable gate passed on the closure candidate.

### Live proof

No dedicated live proof is required. Run the ordinary short launch/run smoke only if schema or serialization changes touch the live adapter.

---

## 14. Stage 7 — Move tooling to the perimeter and delete compatibility debris

### Thesis

The runtime core should not carry development orchestration, generated-doc logic, historical configuration, or obsolete live modes.

### Work

1. Move CLI and live-development orchestration behind the public application API.
2. Move doc export, mutation orchestration, evaluations, scenario tooling, graphics setup, and overlay management into an explicit tooling perimeter.
3. Keep the native C++ plugin stable unless an actual adapter requirement demands change.
4. Reduce `config/live.yaml` to canonical policy and actual knobs.
5. Remove configuration fields documented as compatibility-only or unread by the runtime, including current examples such as:

   - `runtime.stop_when_terminated`
   - `capture.crop_client_area`
   - `safety.require_cli_execute_flag`

   Confirm current usage before deletion; do not preserve an unread field because a comment calls it compatible.

6. Remove old planner/action schemas, dead commands, obsolete generated reports, and superseded ADRs.
7. Collapse historical live configurations to fixtures or delete them if they are neither supported nor used by tests.
8. Keep mock and replay as proper adapters, not alternate architectural centers.
9. Retire the macro/skill compatibility surface, or state plainly why it survives.

   `SkillAction` and the configured macro registry are the last operations that
   do not reach the runtime as ordinary operation definitions. They bind and
   execute through the operation kernel, but they keep a parallel
   macro-expanded guard: in `ActionGuard` that is roughly 250 lines across
   `_validate_purchase`, `_validate_native_vendor_target`, and
   `_validate_native_vendor_continuation`, reached by name-matching
   `approach_confirmed_vendor`, `continue_confirmed_vendor_approach`, and
   `buy_inspected_shop_item`. Stage 4 deliberately left that path alone rather
   than pull this work forward, and `safety.py` says so in a comment that
   promises "its own reconstruction stage" - this is that stage.

   Decide per macro whether it becomes a first-class operation definition, is
   absorbed by an existing one, or is deleted. A macro that survives must lose
   its private validation path; `ActionGuard` may not keep a second policy for
   operations reached by skill name.

### Documentation reset

Rewrite `ARCHITECTURE.md` from the implemented dependency graph. It should explain the current system in a few pages without requiring the reader to traverse dozens of ADRs.

Existing ADRs should be classified:

- Current invariant.
- Historical rationale.
- Superseded and deleted.

Do not retain contradictory accepted ADRs.

### Structural gates

At stage exit:

- Application/runtime code does not import CLI, live-dev, docs, evals, overlay, or mutation tooling.
- Canonical live config contains no knowingly unread compatibility field.
- Public CLI commands resolve through one application composition root.
- No dead command is mentioned in authored documentation.
- Core/application classes remain below the reconstruction size thresholds or have an explicit, reviewed reason.

### Exit criteria

- Tooling is peripheral.
- Config represents actual behavior.
- Architecture docs describe the new system rather than layering amendments over the old one.
- Superseded compatibility paths are gone.
- Portable gates pass.
- Native setup and build documentation still works.

**Closed 2026-08-04.** `application.py` is the one public composition root;
`cli.py` and the supported live-development launcher are outer adapters that
enter it. Development orchestration, scenario fixture storage, evaluations,
overlay, graphics setup, mutation campaigns, registry audits, and generated
document/schema logic now live under `kenshi_agent.tooling`. Fitness tests
require the inward production graph to import neither that perimeter nor the
CLI, require the public adapters to resolve through the application root, and
retain the Stage 6 acyclic import-graph gate.

The generic `SkillAction`, macro registry, configured macro schemas, name-based
macro policy, and their old tests/examples are deleted. Proven native talk and
movement behavior now arrives as typed operation definitions; the fixed native
command trigger and bounded movement pulse are ordinary adapter mechanics. The
external protocol's optional `CalibrationIdentity.macro_set_hash` remains
parseable so the unchanged native plug-in stays compatible, but Python has no
macro execution owner. Mock and replay remain environment adapters on the same
coordinator/kernel path and expose no skill operation.

The canonical configs lost unread compatibility fields and historical macro
identity/configuration. `config/live.yaml` was reduced to its actual OpenRouter,
PNG capture, control, launch, safety, and continuity knobs. Obsolete blocker and
mutation ledgers, their generated reports, dead CLI commands, calibration and
planner-macro examples, missing package-document references, and compatibility
re-exports are gone. Every surviving modeled config field has a production
consumer. `ARCHITECTURE.md` now describes the implemented dependency graph,
run and operation lifecycles, authority/evidence owners, adapters, persistence,
and tooling boundary directly; no accepted ADR set survives to contradict it.

The full portable gate passed after generated schemas and documents were
refreshed: pytest, Ruff, strict mypy across 142 source files, deterministic
schema/document export, and diff hygiene. The native and game-source trees were
unchanged. The public live proof then passed: `./dev launch` reached a loaded,
paused world; run `stage7-tooling-live-proof-20260804c` observed, planned, bound,
executed, and confirmed one typed screen transition through the application
root and finalized paused at telemetry sequence 910; `./dev recover` confirmed
pause, no unresolved modal, and no display lease; and `./dev stop` closed Kenshi
from a fresh paused idle state. An earlier successful operation exposed a Piper
WAV cleanup race at wrapper exit; cleanup now waits for the synchronous player
to release ownership, its concurrency regression test passes, and the clean run
was repeated after the complete portable gate.

### Live proof

Run launch, short run, recover, and stop through the public `./dev` commands to prove that moving the composition root did not break operator workflow.

---

## 15. Stage 8 — Final proof, deletion audit, and merge

### Thesis

The reconstruction is complete only when the old architecture is absent and the surviving system proves the same external competence.

### Required absence checks

The final tree must contain none of the following:

- `ActionContract` or `ACTION_CONTRACTS`.
- Universal `ReferenceBinding`.
- `AgentEnvironment.step()`.
- A semantic action switch in the live environment.
- A central `_execute_step()` action switch.
- Separate `_run_single_step()` and `_run_continuous()` implementations.
- Universal `models.py`.
- Compatibility re-export modules for deleted architecture.
- Unread compatibility fields in canonical live config.
- Two owners for completion, authorization, outcome assessment, or finalization.
- A temporary legacy executor or migration fallback.

### Required presence checks

The final tree must have:

- One planner-visible affordance language.
- One private operation-definition registry.
- One operation execution kernel.
- One handler per private operation.
- One run coordinator.
- One planner-context assembler.
- One cross-cutting operation authority with input-boundary revalidation.
- One outcome recorder.
- One continuity service.
- One final-state owner.
- One independent supervisor.
- Narrow Kenshi, mock, replay, planner, and storage adapters.
- An acyclic package dependency graph.
- Generated completeness reports derived from current registries rather than handwritten lists.

### Final verification matrix

#### Portable

- Full Python suite.
- Ruff.
- Mypy.
- Schema generation and staleness gate.
- Documentation generation and staleness gate.
- Mock single-cycle scheduling through the same coordinator.
- Continuous mock scheduling through the same coordinator.
- Replay of representative operation traces.
- Restart continuity evaluation.
- Human handoff and supervisor deterministic tests.
- Import-direction and cycle gates.
- Deletion/absence gates.

#### Native

- Release x64 build.
- Protocol conformance fixtures.
- Installed DLL hash/protocol check.
- Telemetry freshness and stable identity smoke.

#### Supervised live

1. Launch or load through the canonical command.
2. Verify current affordance enumeration.
3. Execute exact squad selection or whole-party operation.
4. Complete one native movement/dialogue operation.
5. Complete the harvest/sell/buy economic loop or an equivalent UI/native/composite vertical proof.
6. Exercise an active-interface transition and exact window ownership.
7. Interrupt one operation through human handoff or F12.
8. Recover or stop.
9. Finish with fresh telemetry confirming pause and no owned windows left open.
10. Run a moderate autonomy soak only after the vertical proofs pass. Thirty to fifty strategic selections are sufficient for reconstruction acceptance; broader competence belongs to the next frontier.

### Final deletion report

Before merge, produce a concise report containing:

- Files deleted.
- Major classes and methods deleted.
- Net source/test line change.
- Dependency cycles removed.
- Former authorities and their replacements.
- Compatibility fields and modes removed.
- Portable/native/live evidence IDs.
- Remaining limitations that are genuine gameplay or telemetry limitations rather than architecture debt. Section 19 records the ones observed during reconstruction.

### Completion state

Reconstruction is complete when all Stage 8 absence, presence, and verification checks pass on one commit and the branch can be merged without retaining any temporary fallback.

The presence of additional possible abstractions, stylistic improvements, or gameplay ideas is not evidence that reconstruction is incomplete.

**Closed 2026-08-04.** The final tree passes the named absence and single-owner
fitness gates, has zero production import cycles, and retains no legacy
executor, macro/skill mode, compatibility re-export, unread canonical config
field, generated blocker queue, or duplicate finalization owner. The complete
portable gate, fresh Release x64 native build and protocol fixtures, installed
DLL/protocol check, durable supervised verticals, current public live smoke,
human-handoff proof, recover/stop proof, and 47-selection moderate soak satisfy
the final matrix without adding gameplay frontier work.

The [Stage 8 acceptance report](reconstruction/stage_8_acceptance.md) records
the deletion and line deltas, authority replacements, removed compatibility,
native artifact hashes, bounded evidence IDs, claim boundaries, and deferred
gameplay/perception limitations. The accepting commit carries the tag
`reconstruction-stage-8-accepted`.

---

## 16. Stage sequencing and rollback boundaries

Each stage should land as a self-contained checkpoint on the reconstruction branch. Tag or name the last green commit of each stage:

```text
reconstruction-stage-0-baseline
reconstruction-stage-1-operation-contracts
reconstruction-stage-2-execution-kernel
reconstruction-stage-3-run-coordinator
reconstruction-stage-4-authority
reconstruction-stage-5-context-continuity
reconstruction-stage-6-core-types
reconstruction-stage-7-tooling
reconstruction-stage-8-accepted
```

Rollback means returning to the previous stage checkpoint, not reviving pieces of the old subsystem inside the current stage.

A stage should be rolled back or redesigned when:

- The new owner cannot be stated in one sentence.
- The stage increases the number of registries or dispatch sites.
- Old and new implementations both remain at exit.
- Tests pass only because behavior is duplicated in both paths.
- A new compatibility layer has no scheduled deletion.
- Generated audits derive from more than one authority.
- The stage changes external behavior without an explicit version or acceptance decision.
- The stage cannot produce its required vertical proof.

Do not roll back merely because the diff is large.

---

## 17. Codex operating directive

Use the following posture for every reconstruction stage.

> Work boldly within the named stage. The repository is already backed by git, known-good tags, deterministic portable tests, and supervised live operation. Do not optimize for tiny diffs or continuous green status during intermediate edits. Optimize for ending the stage with one owner and the old owner deleted.
>
> Read historical ADRs and tests as evidence, not as blanket preservation requirements. Preserve externally supported behavior and epistemic guarantees. Break internal APIs freely. Do not build a second permanent architecture beside the first.
>
> Begin by naming the authority being removed and the authority that will replace it. Migrate the complete bounded responsibility, update behavior-focused tests, delete the old path, regenerate artifacts, and run the stage gate. Use targeted tests during construction and the full suite at stage completion.
>
> Do not add gameplay capabilities, speculative abstractions, compatibility options, audit frameworks, or defensive layers unrelated to the stage. Do not respond to a difficult replacement by leaving both systems in place. A temporary fallback may own only unmigrated cases and must be deleted by the stage’s stated exit.
>
> Treat ordinary Kenshi gameplay effects as ordinary game effects. Preserve exact host-input ownership, human handoff, telemetry freshness, causal native terminals, and final safe-state confirmation. Do not use those concerns to justify preserving redundant internal machinery.
>
> At completion, report what was deleted, which single owner remains, which tests prove it, which generated artifacts changed, and whether the stage’s live proof was required and passed.

---

## 18. Recommended first execution slice

Do not begin by splitting `models.py` or extracting random helpers from the three largest classes.

Begin with Stage 0, then Stage 1’s runtime/cognitive cohort as the smallest complete proof of the new private operation definition:

- noop
- stop
- consult advisor
- recall memory
- read fieldbook

These operations exercise planner provenance, private binding, controller-terminal completion, zero-input execution, receipts, and continuity without depending on complex UI mechanics. They establish the new operation-definition shape cheaply.

Then migrate screens/bindings, movement/dialogue, trade/inventory, and resources/composites in that order. Do not stop Stage 1 until `action_contracts.py` is deleted.

That is the first real demolition checkpoint.

---

## 19. Deferred gameplay and perception findings

Observations made while proving a reconstruction stage that are **not**
architecture debt. Nothing here is a reason to change a stage's scope: Section
6.4 forbids frontier work during reconstruction, and each of these is gameplay
competence or perception. They are written down because a live run surfaced
them, they will recur, and they belong in the Stage 8 report and the frontier
work that follows.

### 19.1 Prospecting scalars are area coverage, not deposit counts

Observed in `reconstruction-stage-4-r1` at Squin, 2026-08-03.

Kenshi's Prospecting Results window reports each resource as a number beside its
name and, separately, draws a radar-style panel: the prospector as an arrow at
the center, and deposits of the currently selected resource as bright blobs
positioned relative to them.

The number is the share of the scanned area the resource covers, against the
window's own `Area Size` (414 m in the observed capture). Diffuse resources
score meaningfully - water 60, fertility 100, stone 10 - because they blanket
the zone. A discrete deposit occupies a trivial fraction of that area, so
**iron and copper read `0` while deposits are plainly drawn on the panel**.

The agent read `Iron: 0` as "no iron here", abandoned a zone whose panel showed
two iron deposits, and travelled elsewhere. The two facts never conflicted; it
consulted the wrong one.

What this actually means:

- The only channel carrying deposit *locations* is the spatial panel, and the
  agent has no way to read it. Its one legible channel is the scalar, and for
  the resources worth mining that scalar is misleading by construction.
- Treating the number as a count will keep producing confident wrong
  conclusions, because `0` is the expected reading for a present node.
- A fix is perception work - reading the panel, or exporting deposit positions
  through telemetry so the affordance layer can offer them - not a planner
  prompt adjustment.

### 19.2 A stalled monitored approach can consume minutes of a run's budget

Observed in `reconstruction-stage-4-r1`, 2026-08-03.

One `approach_dialogue_target` ran five minutes before ending on its step
timeout, after the character walked into a building and stopped making progress.
A later `move_to_character` ended as `cancelled: movement_stalled`.

The machinery behaved correctly and this is not a defect in it: the native
command reached a causal terminal, the reflex reclaimed ownership and re-paused,
and the run continued. The cost is budgetary. Five minutes against a
thirty-step run is a large fraction of the session spent proving one approach
did not work, and a run can exhaust itself on navigation before reaching the
behavior under test.

Worth considering after reconstruction: a progress-based stall terminal that
ends an approach when position stops changing, rather than waiting out a
wall-clock timeout that is sized for the slowest legitimate walk.

### 19.3 A run bundle cannot say why an affordance was not offered

Observed repeatedly while post-morteming `reconstruction-stage-2-vertical-r2`
and `reconstruction-stage-4-r1`, 2026-08-03.

Run bundles record the affordance that was *chosen* - `affordance_receipt` with
its lifecycle - and never the menu it was chosen from. `planner_context_prepared`
carries `current_target_ids` and counts, not offers. Observations are stored as
digests, so `squad` and `ui.selected_character_ids` are absent entirely.

The consequence is that the most common question after a disappointing run -
"why didn't it do the obvious thing?" - is not answerable from the evidence. It
has to be reconstructed by reading enumeration code, re-deriving gate conditions
by hand, and inferring the offer set from telemetry fragments. That inference
was wrong more than once in a single session, and being wrong is cheap to do
confidently, because nothing contradicts it.

Recording the offered affordance set per planner context - ids, operation kinds,
and for anything enumerated but withheld, the gate that withheld it - turns an
hour of archaeology into one query. It is also the only way to distinguish
"the model ignored a good option" from "the option was never on the menu",
which are different problems with different fixes.

### 19.4 Authorability gates can be stricter than the invariant they protect

Observed in `harvest_resource`, 2026-08-03.

`bind_harvest_resource` binds an exact actor by identity:

```python
selected = [c for c in telemetry.squad if c.selected and c.id == action.actor_id]
```

That line is what makes the actor exact. The binding then *additionally*
requires `telemetry.ui.selected_character_ids == [action.actor_id]` - that the
squad selection be narrowed to precisely that one character - and
`harvest_resource_is_currently_authorable` repeats the same demand before the
affordance is offered at all.

The singleton requirement is plausibly a need of the final inventory transfer,
which drives one unambiguous recipient panel. It is not a need of travelling to
the node or of mining, yet it gates the whole operation. A squad can walk to a
resource together and have its primary actor work it; the exactness that
conservation depends on comes from `actor_id`, not from the selection set being
a singleton.

The practical cost is that a multi-character start - `kae-03-broke-pair`, for
instance - cannot harvest at all until something first narrows the selection,
and nothing says so. The affordance is simply absent, with no stated reason,
which is 19.3's problem wearing a different hat.

Worth revisiting afterward: scope such gates to the phase that needs them.
Requiring at bind time what only one late phase depends on removes an operation
from the menu for situations it would have handled correctly.

---

## 20. Named residue after Stage 4

Stages 2, 3, and 4 removed their headline authorities, but each left boundary
work that the stage's own exit criteria demand. This section names that residue
so it is closed deliberately rather than encoded into the package graph by
Stage 6.

Recorded 2026-08-03 after an external review of the post-Stage-4 snapshot. Each
item below was re-verified against the source rather than accepted on report.

**Do not roll back the completed work.** These are closures, not redesigns.

### 20.1 Stage 2 closure: the external surface only delivers

Closed 2026-08-04 with Stage 4. The surviving boundary is:

```text
operation handler / authority
  -> narrow input or native port
  -> Kenshi external adapter
```

`KenshiControlSurface.classify_pointer_action()`, `rebind_in_lease()`, and
`_is_task_start_only()` are deleted. The surface receives the pointer class,
fresh authorized binding, and handler-owned terminal facts it needs for
delivery; it no longer imports operation definitions, binds references, or
selects operation terminal policy. A fitness check holds that boundary.

The independent supervisor and control-ownership machine retain one explicit
pause transport. It deliberately does not require a plan execution token:
human input and F12 are the conditions under which supervision must still be
able to establish pause. `RunCoordinator` owns the decision and receives only
that narrow delivery capability; it no longer receives the general operation
mechanics port.

### 20.2 Stage 3 closure: one physical coordinator and a plan-local executor

`_run_single_step` and `_run_continuous` are gone and both schedules share one
loop, one planner-call lifecycle, one error route, and one finalization. The
loop itself contains no operation-family logic, and a fitness gate holds that.

Closed 2026-08-03 after the Stage 5 services existed. `_run_scheduled` and its
run-session/finalization machinery now live physically on `RunCoordinator`;
`AgentRuntime` composes the services and delegates `run()`.

`ContinuousPlanExecutor` now owns only DAG traversal, dependency and branch
handling, step retries, operation submission, plan-local cancellation, and
plan-local budget/condition checks. It no longer imports or composes an
environment, operation mechanics, handlers, execution kernel, monitor,
operation authority, planner context, future planner, memory or fieldbook
service, advisor service, continuity callback, or outcome recorder.

`OperationExecutionService` is the narrow operation-submission boundary and
`FuturePlanningPolicy` owns the optional concurrent advisory lifecycle, patch
validation, activation, and exact continuity commit. The old non-plan-local
executor APIs and the scheduler implementation on `AgentRuntime` are deleted.

Evidence: the full portable gate passed, and supervised live run
`reconstruction-stage-3-run-coordinator-r3` completed 11 accepted plans,
including a monitored native movement with repeated progress observations and
an exact success terminal, replanned afterward, stopped explicitly with zero
plan failures, and recorded `pause_confirmed` final safety.

### 20.3 Stage 4 closure: authority identity, rebinding, policy, and budget

Closed 2026-08-04. `OperationAuthority` evaluates a `BoundOperation` and returns
one typed `AuthorizationDecision` before scheduling and again inside the input
lease. Immutable `OperationIdentity` fingerprints the definition version,
operation request, affordance provenance, and stable binding identity while
excluding volatile revision and geometry. The boundary carries the freshly
rebound operation and exact observation forward to the handler.

`OperationBindingAuthority` is the only executable fresh-binding
implementation. Operation handlers consume its binding instead of independently
calling operation-definition binders. `live_plan_policy.py` is structural only;
current capability, selection, binding, control-mode, and non-progress
eligibility remain with operation definitions, affordance binding, and
`OperationAuthority`.

`ActionGuard` is deleted. Pure `OperationPolicy` owns cross-cutting operation
rules, while mutable `ActionBudgetLedger` owns reserve, commit, release, rate,
and purchase accounting. The kernel coordinates them without merging them.
Binding absent, binding ambiguous, capability unavailable, selection invalid,
policy disallowed, transaction budget unavailable, and stale bound identity
are stable typed refusal codes.

Independent supervision remains independent. `SafetySupervisor`,
`ControlOwnershipMachine`, `ReflexEngine`, and the final-state owner retain
separate event streams and responsibilities. The coordinator receives a narrow
control-pause delivery callback, not operation authority or general operation
mechanics.

Evidence: the full portable gate passed. Supervised live run
`reconstruction-stage-4-control-boundary-r5` began monitored native travel,
recorded human-input preemption, cancelled the operation, causally confirmed
pause, emitted human ownership with no operation input during the handoff,
completed the visible 5-to-1 automatic-takeover countdown, replanned from fresh
state, then accepted F12 and causally confirmed a second pause. Both native
travel commands reached `cancelled/world_paused` terminals, and final safety
recorded `pause_confirmed` with no additional finalizer input.

### 20.4 Closure order completed

Stage 5 extraction, Stage 3 closure, and the combined Sections 20.1/20.3 Stage 4
closure are complete. Stage 6 is also closed as recorded in Section 13. Stage 7
and the final Stage 8 acceptance are closed as recorded in Sections 14 and 15.
Do not reopen these stages or restore deleted compatibility boundaries. Further
work begins from the accepted architecture and belongs to a separately scoped
gameplay or perception frontier.
