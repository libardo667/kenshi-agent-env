# Kenshi Interaction Scope and Order Lifecycle Reconstruction

**Status:** Proposed post-reconstruction architecture stage  
**Working name:** Interaction Scope and Order Lifecycle Reconstruction  
**Placement:** Begin only from a tagged, accepted Stage 8 commit. If numbered, this becomes Stage 9 rather than being folded retroactively into Stage 8. Do not let this stage absorb, excuse, or silently waive an unfinished Stage 8 gate.  
**Primary objective:** Make the agent's interaction model match Kenshi's selection-broadcast, asynchronous, squad-based play model without surrendering the reconstructed system's exact identity, causal authority, typed operation registry, or safety boundaries.  
**Revision note:** This version folds in the architectural lessons exposed while executing Stages 0–8: accepted checkpoints and rollback boundaries, same-operation identity across fresh rebinding, single-owner planner/outcome/finalization services, versioned evidence, generated audits that do not become rival registries, plan synchronization at asynchronous milestones, idempotent native delivery, and cold residue audits.

**Reconciliation note (2026-08-04):** The plan was originally drafted against an
uploaded Stage 8 snapshot rather than the live repository. It has since been
reconciled against the working tree at `HEAD`. §2 now separates what was
verified in-tree from what is stale framing and what cannot be checked here at
all; §2.4 records post-acceptance drift; §3.5 and the corresponding ratchet were
rewritten from an API prohibition into an ownership rule; the unimplemented
queue-policy member was removed; Slice 1b was split out of Slice 4 so honest
evidence vocabulary precedes the risky native work; Slice 2's dual state was
named with a deadline; Slice 0 gained an installation-rollback requirement; and
§14 now separates structural conditions from capability conditions.

---

## 1. Executive decision

The next change must **not** be a bulk replacement of `EXACTLY_ONE` with `ONE_OR_MORE`.

`SelectionRequirement` is the wrong abstraction because it collapses several independent facts:

1. Which part of Kenshi an interaction addresses.
2. Which characters receive an order.
3. Whether the current UI selection matters only at dispatch or throughout a short UI transaction.
4. Whether Kenshi accepted an order, is currently carrying it out, or produced the intended world outcome.
5. Whether the Python monitor is still observing the order.
6. Whether another order may coexist on different recipients.
7. Whether an overlapping order supersedes prior work.

The correct reconstruction is to replace selection cardinality with a typed **interaction contract**, capture an immutable **dispatch basis** at the final input boundary, and model Kenshi orders as plural, recipient-bound, asynchronous game state.

The architecture should retain one serialized host-input lane. Kenshi order concurrency does not require simultaneous keyboard or mouse delivery. It requires the controller to issue one bounded input transaction, release the lane, then retain and monitor the accepted game order while another transaction is delivered to a different group.

The intended causal chain remains:

```text
AgentRuntime
  -> RunCoordinator
  -> OperationExecutionService
  -> ExecutionKernel
  -> exact operation handler
  -> narrow interaction mechanics
  -> Kenshi order/UI state
```

The reconstruction changes the vocabulary and lifecycle within that chain. It does not create a rival executor, a second run loop, or a planner-visible primitive control language.

---

## 2. Evidence and certainty boundary

### 2.1 Verified in the working tree

Re-verified against the working tree on 2026-08-04, five commits past
`reconstruction-stage-8-accepted`. Line references are to that state and will
drift; the facts they anchor are what matters.

- `OperationDefinition` owns a `SelectionRequirement` with only `NONE`, `EXACTLY_ONE`, and `ONE_OR_MORE` (`operation_definitions.py:137`), gating all 25 registry entries, and re-read by `safety.py:137-142`. It appears in no test, so deleting it is a source-only change.
- Python request validation and the native request parser duplicate command-specific cardinality exceptions.
- `KenshiAgentTelemetry.cpp` owns one global `g_activeNativeCommand` (`:211`), cleared field by field (`:256-264`), and rejects a second request as `command_already_active` (`:2677`).
- Native monitoring compares the **current** selection with the dispatch selection and terminates with `selection_mismatch`. The split matters for Slice 3: `:2758`, `:3078`, and `:3089` are dispatch-time validation and are defensible; `:2049`, `:2268`, `:2288`, `:2357`, and `:2516` are post-dispatch monitoring and are the sites that make an unrelated player click cancel a running order.
- Native map travel can issue a later interior-leg order through `newPlayerTaskSelectedCharacters` (7 call sites), meaning the continuation can accidentally target whichever characters are selected later unless it explicitly restores the captured recipient basis.
- `OperationMonitor` calls `option.cancel()` on timeout, strategic interruption, and supervisory cancellation.
- A stateful native option's cancellation stops the host-side task and reports `OptionStatus.CANCELLED`; that does not prove Kenshi's underlying order was cleared.
- Native command monitors treat a paused world as cancellation and toggle global pause when an individual command finishes (`togglePause(true)` at `:2400` and `:2547`).
- `TelemetrySnapshot.squad` is used as the player-character collection while selection is represented separately in `UIState`; there is no first-class platoon model. Whole-roster "select all" is offered from `affordances.py:703-737`.
- `NativeControlState` exposes one `active_command_id`, consumed in at least eight Python modules including `condition_evaluation.py:184`, `observation_budget.py:534`, and `options.py:544`. Host-side plural lookup replaces `_active_native_order_for()` at `execution/handlers/kenshi_surface.py:1034`.
- Affordance code derives the operative character from the first roster member carrying `selected=True` instead of using `ui.selected_character_id` (`affordances.py:674`, `:842`, `:881`; `live_native_smoke_planner.py:50`).

### 2.2 One finding whose framing has changed

Resource cleanup does call `removeJob()`, `clearOrders()`, and `halt()`
(`KenshiAgentTelemetry.cpp:2187-2192`) on work issued as an ordinary
selected-character order. The API-level finding is confirmed.

Its characterization as incidental cleanup is **not** current. Commits
`97a2909` and `5b97e31` deliberately added and then extended this path after
the snapshot this plan was drafted against, with a written ownership argument —
`newPlayerTaskSelectedCharacters(..., false)` replaced the actor's prior
ordinary order queue, so the command owns that queue — and a release
confirmation window around the mutation.

This plan must therefore prohibit the **defect**, not the API. See §3.5. A
blanket ban on calling `removeJob()` during ordinary-order cleanup would
invalidate reasoned recent work, and the ratchet in §12 could not land without
reverting it. The ownership claim in that comment is an empirical bet about
Kenshi's Job/order relationship and is a live-proof obligation under §13.6, not
an established fact.

### 2.3 Not reproducible in this repository

The Kenshi SDK headers are external (`#include <kenshi/...>`) and are not
vendored here. The claim that Kenshi's headers distinguish ordinary orders,
Jobs, and permajobs, and that resource objects expose operator
capacity/current operators, **could not be verified in-tree**. It must be
checked against a local SDK before Slice 2 commits to exporting those fields.
Its unverified status is not a reason to discard it; it is a reason not to plan
Slice 6's telemetry shape as though it were settled.

One supporting signal does exist in-tree: `kenshi/Platoon.h` is already
included and `ActivePlatoon` is already used (`:737`, `:4029`) for
`getPlatoon()`, `getSquadLeader()`, `getIsTrader()`, and `getHasVendorList()`.
The platoon model in §9.1 is reachable from an API surface the plug-in has
already exercised on NPC targets; it has simply never been exported for player
characters. Treat platoon export as low-risk relative to Jobs and operator
capacity.

### 2.4 Post-Stage-8 drift

This plan was drafted against the Stage 8 acceptance snapshot. `HEAD` is five
commits past tag `reconstruction-stage-8-accepted`, and four of those commits
land inside this stage's territory: `d6fcbfd` (selection-set change
recognition in the outcome recorder), `97a2909` and `5b97e31` (resource order
release, whole-group travel), and `3b472bb` (group-aware map travel).

One item is new residue rather than progress. `3b472bb` and `5b97e31` threaded
a `selected_count` parameter into `map_destination_already_reached`
(`core/telemetry.py:346-359`), so a selection-cardinality rule now lives in a
telemetry helper, outside the operation registry. Slice 1's gate forbids
exactly that. It is small and it was written for a good reason, but it is the
one-line-permission-change pattern §16 warns against, and Slice 1 must absorb
it rather than route around it.

Slice 0 therefore baselines at `HEAD`, not at the Stage 8 tag, and records this
delta explicitly.

Several gameplay semantics still require targeted live proof and are listed later. Unknown behavior must stay unknown rather than being filled in by a broad generic control path.

---

## 3. Reconstruction laws

### 3.1 One authority per concept

At stage exit, each concept has one owner:

| Concept | Sole owner |
|---|---|
| Operation semantics | `OperationDefinition` registry |
| Resolved interaction scope | the operation definition's interaction contract/resolver |
| Fresh current-state binding | existing `OperationBindingAuthority` |
| Cross-cutting authorization | existing `OperationAuthority` |
| Final dispatch recipients | immutable `DispatchBasis`, materialized from the authorized fresh binding at the input boundary |
| Host input serialization | existing input lease/input boundary |
| Controller-issued native command records | native bridge command registry |
| Recipient-level native order lifecycle | native command record plus exported telemetry |
| Host monitor attachment | Python operation monitor/option lifecycle |
| In-run playback reconciliation | one narrow runtime playback coordinator, never an individual native command monitor |
| Run sequencing | `RunCoordinator` |
| Planner-visible retained-order projection | `PlannerContextAssembler` |
| Run-level outcome semantics | `OutcomeRecorder` |
| Durable continuity commits | `ContinuityService` |
| Final pause decision and causal confirmation | existing `FinalSafeStateOwner` |
| Terminal result for one operation | the existing terminal-owner machinery, expanded to understand asynchronous milestones |

No second selection rule may live in transport validation or native parsing. Those layers validate the resolved contract and dispatch basis; they do not independently decide which commands are group-capable.

The new scope resolver must not become a second binder or guard. `OperationBindingAuthority` remains the only executable fresh-binding implementation, and `OperationAuthority` remains the only cross-cutting policy owner. A dispatch-basis resolver is a pure materializer that consumes the authorized freshly rebound operation and observation. It does not re-enumerate affordances, call binders directly, or make a second policy decision.

### 3.2 Capture at dispatch, monitor by recipient

Selection is a dispatch mechanism, not permanent command ownership.

For a selection-broadcast order:

1. The scheduled operation already carries one stable `OperationIdentity` and authored recipient intent.
2. The execution kernel obtains the input lease.
3. `OperationBindingAuthority` performs the sole fresh rebind against the exact input-boundary observation, and `OperationAuthority` proves that the rebound operation still has the scheduled identity.
4. A pure dispatch-basis materializer consumes `AuthorizationDecision.bound_operation` and its authorized observation to resolve primary, selected IDs, active platoon, and exact recipients. It makes no second binding or policy decision.
5. The native bridge issues the order to those recipients and acknowledges the same command/request fingerprint.
6. The dispatch basis becomes immutable.
7. Later monitoring follows captured recipient handles and order evidence, never the current UI selection.

After step 6, the player or agent may select another character immediately unless the operation is still inside a short, explicitly declared UI transaction.

### 3.3 Plural game orders, serialized input

The architecture must distinguish:

- **Input delivery:** one bounded transaction at a time.
- **Kenshi orders:** many retained orders may coexist on disjoint recipients.
- **Python operation monitoring:** remains foreground and serialized under the current coordinator. A routine assignment returns when its required milestone is reached; native telemetry continues tracking the retained order.
- **Planner reasoning:** the planner sees which recipients are occupied, what order they hold, and whether the order is accepted, active, suspended, completed, replaced, or retained without a foreground monitor.

This stage does not introduce parallel `SendInput`, concurrent UI clicking, multiple display owners, or a new background Python scheduler. Plural monitoring belongs primarily in the native registry and ordinary telemetry refresh.

### 3.4 Monitor detachment is not order cancellation

A timeout, plan revision, run termination, process shutdown, or safety interruption may stop host observation. None of those events proves that Kenshi cleared the order.

The system may say:

- monitor detached after timeout;
- monitor detached after strategic interruption;
- order was retained at last observation;
- order disposition is unknown after telemetry loss;
- order was explicitly cleared;
- order was replaced by command X;
- order naturally ended without the requested outcome;
- outcome was observed.

It may not say “cancelled” unless an exact underlying Kenshi clear action was deliberately issued and verified.

### 3.5 Jobs, permajobs, and ordinary orders are separate channels

Ordinary orders, Jobs, and permajobs are distinct channels and must not be
conflated in telemetry, in planner-visible vocabulary, or in mutation.

The rule is **causal ownership, not API avoidance**. A command may mutate order
or Job state it can show it created; it may not mutate state it merely found.
Concretely:

- Mutation requires a recorded ownership claim: which dispatch established the
  state being cleared, and what evidence proves the claim.
- The ownership claim must be *proven*, not asserted in a comment. If a command
  clears a Jobs entry on the theory that its own ordinary order created that
  entry, that theory is a live-proof obligation (§13.6), and until it is proven
  the behavior is withheld or explicitly marked unproven in the interaction
  catalog.
- No operation may clear an entire ordinary-order queue, or any Job or
  permajob, merely to stop observing one command. Breadth of clearing must be
  bounded by breadth of ownership.
- Pre-existing, human-issued, or AI-issued work is never in scope for cleanup,
  in any channel.
- If the available API cannot prove exact clearing, the controller must not
  expose a false exact-clear operation.

The existing resource release path (§2.2) is the live case. It is not
automatically a violation; it is an unproven ownership claim that must either
earn its proof or narrow its scope.

### 3.6 In-run playback reconciliation has one owner

Pause and speed affect every character and every order. They cannot be command-local cleanup.

Individual native monitors must never toggle pause because one recipient arrived, stalled, or produced output. They report evidence only. A single runtime playback coordinator owns ordinary desired simulation state and UI-sensitive temporary pauses. `SafetySupervisor` and `ControlOwnershipMachine` retain independent preemption; `FinalSafeStateOwner` retains terminal cleanup and confirmed pause.

A paused world suspends progress. It does not cancel orders.

### 3.7 Unknown controls stay withheld

A visible button, binding, or native method is not automatically a semantic gameplay affordance.

Controls such as Prospect, editor/build functions, squad-group bindings, or mode-sensitive toggles remain unavailable to the planner until the controller can state:

- their interaction kind;
- their recipient scope;
- their preconditions;
- their observable milestone or outcome;
- their playback requirements;
- their conflict behavior;
- and their live proof boundary.

### 3.8 Exact authored recipient identity survives fresh rebinding

`CURRENT_SELECTION` means the exact selection that made the selected affordance authorable and that is freshly revalidated at dispatch. It does **not** mean “whoever happens to be selected by the time input lands.”

The resolved interaction contract and the intended primary, selection, or platoon basis must participate in stable operation identity. The final input-boundary rebind must prove that it is still judging the same operation and intended recipients. If the selection, primary, active platoon, dynamic context-action semantic, or resolved contract changed, fail with a typed stale-binding result and replan rather than silently redirecting the order.

The immutable `DispatchBasis` records what was finally issued only after that identity check passes.

### 3.9 Assignment success is not world-goal achievement

`ORDER_ACCEPTED` and `ACTIVITY_RUNNING` are honest success milestones for an assignment. They are not evidence that travel finished, dialogue opened, output was produced, or another world objective was achieved.

Operation results, plan-step records, planner feedback, outcome assessment, and continuity evidence must preserve that distinction. A step may be reported as “assignment accepted” while the retained order continues. It may be reported as “goal achieved” only after the declared world-outcome milestone is causally observed.

No dependent plan step may inherit a world-outcome assumption merely because an assignment step reached its lower milestone. It must either revalidate exact recipient/target preconditions, await an exact retained-order/world condition, or belong to a later replan from fresh evidence.

### 3.10 Controller causality is not the whole Kenshi order state

The native command registry records commands this controller issued and the evidence causally associated with them. It is not the canonical inventory of every Kenshi order. Ordinary order, Job, permajob, and current-task telemetry remain world state and may include pre-existing, human-issued, AI-issued, or otherwise unattributed activity.

When task evidence changes without a causally linked controller command, report an external or unattributed replacement. Do not invent a `superseded_by_command_id`. Human input, combat AI, incapacitation, save/load, and game logic can alter tasks outside the controller registry.

### 3.11 Persisted evidence is versioned, never silently reinterpreted

The live telemetry/native protocol may make a clean breaking transition with no dual semantic model. Persisted run evidence is different: old bundles that recorded `cancelled` under the former host-monitor semantics must remain readable as historical versioned evidence or receive an explicit migration.

Introduce versioned lifecycle/receipt fields for monitor disposition, reached milestone, retained-order references, and native order disposition. Do not silently reinterpret an old `OptionStatus.CANCELLED` record as if it had always meant monitor detachment.

### 3.12 Composite phases remain private to one operation lifecycle

Phase-scoped interaction does not authorize nested planners, nested execution kernels, a second operation registry, or a second strategic action counter. A composite handler may use typed private phase descriptors and narrow mechanics under one operation identity, budget reservation, and terminal owner. Internal selection restoration, order dispatch, progress observation, inventory transfer, and UI cleanup do not re-enter the scheduler.

### 3.13 Operation terminals are exactly once; retained-order history is append-only

Each scheduled operation receives one immutable terminal result from the existing terminal owner. Later native lifecycle facts do not reopen the operation, mutate its receipt, or retroactively change the plan step from assignment success into world-outcome success.

Later facts—activity beginning, partial recipient adoption, supersession, natural ending, external replacement, eventual world outcome, or provenance becoming unknown—are appended as typed lifecycle/outcome events linked by operation ID, command ID, recipient IDs, and world revision. `OutcomeRecorder` remains the sole run-level producer of those causal records.

This preserves the Stage 2 and Stage 5 guarantees simultaneously: one operation terminal, one outcome producer, and a truthful history that can continue after the foreground operation has ended.

### 3.14 Progress clocks follow simulation and captured subjects

A wall-clock timeout is not automatically evidence of gameplay non-progress. Progress/stall accounting for retained work must use the captured recipients and target, and must distinguish time in which Kenshi could actually advance from time spent paused, under stale telemetry, detached, or waiting for host ownership.

Paused or stale intervals do not count as recipient non-progress. A progress-aware stall may end or detach the foreground monitor with a typed reason, but it does not claim that Kenshi cleared the underlying order. Exact thresholds remain operation policy; the lifecycle vocabulary and evidence boundary are architectural.

---

## 4. Target vocabulary

The exact names may be adjusted to fit repository conventions, but the dimensions must remain orthogonal. Do not replace `SelectionRequirement` with one larger enum containing combinations such as `PRIMARY_UI_UNTIL_COMPLETE` or `GROUP_ASYNC_ORDER`.

The repository already has `ExecutionScope` for correlation identity. Do not reuse that name.

### 4.1 Interaction kind

```python
class InteractionKind(StrEnum):
    RUNTIME_ONLY = "runtime_only"
    GLOBAL_UI = "global_ui"
    SELECTION_MUTATION = "selection_mutation"
    ORDINARY_ORDER = "ordinary_order"
    SIMULATION_PROCESS = "simulation_process"
```

Meaning:

- `RUNTIME_ONLY`: no Kenshi input or recipient.
- `GLOBAL_UI`: camera, playback, screens, or another game-wide UI transaction.
- `SELECTION_MUTATION`: changes primary/selected set; it does not command the prior selection.
- `ORDINARY_ORDER`: Kenshi order issued to characters, normally through selection broadcast.
- `SIMULATION_PROCESS`: a semantic process whose useful completion spans order acceptance and world evolution, such as prospecting or bounded resource production. It may internally dispatch ordinary orders but must expose process-specific milestones.

`SIMULATION_PROCESS` is at risk of shipping empty, which would make it the same
kind of unearned promise as the deleted queue member. Its two candidate members
are both uncertain: §10.1 classifies resource production as `ORDINARY_ORDER`
with operator reporting, and §10.4/§13.15 may withhold Prospect entirely.
Slice 1 must name its concrete member set. If that set is empty, delete the
member and let `harvest_resource` express its span through phase descriptors
(§3.12) and `WORLD_OUTCOME_OBSERVED` instead.

### 4.2 Recipient scope

```python
class RecipientScope(StrEnum):
    NONE = "none"
    PRIMARY = "primary"
    CURRENT_SELECTION = "current_selection"
    EXPLICIT_RECIPIENTS = "explicit_recipients"
    ACTIVE_PLATOON = "active_platoon"
```

Meaning:

- `NONE`: no character recipient.
- `PRIMARY`: Kenshi's actual primary character, exported as `ui.selected_character_id`.
- `CURRENT_SELECTION`: all selected characters at final dispatch.
- `EXPLICIT_RECIPIENTS`: stable character IDs carried or resolved by the typed action/binding.
- `ACTIVE_PLATOON`: all members of the exact exported active platoon; never inferred from the entire roster.

A contract may resolve dynamically. `perform_context_action`, for example, may require different contracts for `operate` and `first_aid`. `use_game_binding` cannot have one universal interaction contract merely because all cases share an input mechanism.

### 4.3 Selection dependency

```python
class SelectionDependency(StrEnum):
    NONE = "none"
    DISPATCH_ONLY = "dispatch_only"
    UI_TRANSACTION = "ui_transaction"
```

There is deliberately no “through monitor” value.

- `DISPATCH_ONLY`: the UI selection establishes recipients when the order is issued. Selection may change immediately afterward.
- `UI_TRANSACTION`: primary/selection must remain stable only until the bounded input/UI transaction and its immediate acknowledgement finish. It is not retained through world simulation.
- `NONE`: interaction does not depend on selection.

### 4.4 Completion milestone

```python
class CompletionMilestone(StrEnum):
    INPUT_DELIVERED = "input_delivered"
    ORDER_ACCEPTED = "order_accepted"
    ACTIVITY_RUNNING = "activity_running"
    WORLD_OUTCOME_OBSERVED = "world_outcome_observed"
```

An operation definition chooses the milestone required for that operation's terminal success. The native bridge may report all milestones it observes, but it must not promote “order accepted” into “outcome achieved.”

Routine assignment operations should often finish at `ORDER_ACCEPTED` or `ACTIVITY_RUNNING`, allowing the planner to command another group. Outcome-oriented composites may remain attached until `WORLD_OUTCOME_OBSERVED` or may deliberately detach and later resume from telemetry.

### 4.5 Recipient conflict policy

```python
class RecipientConflictPolicy(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    REJECT_OVERLAP = "reject_overlap"
    SUPERSEDE_OWNED_ORDER = "supersede_owned_order"
```

There is deliberately no queueing member. An earlier draft carried
`ALLOW_PROVEN_QUEUE` with the instruction that it remain unused; a value in the
type system with no implementation is a promise the reconstruction laws would
reject anywhere else, and its presence invites a definition to claim it before
anything is proven. Kenshi queue semantics and the exact dispatch modifier/API
are unproven. Add the member in the same change that proves and implements
queueing, or not at all.

When a new order overlaps only some recipients of an existing group command, lifecycle becomes recipient-specific. The old command may remain active for unaffected recipients while overlapped recipients are explicitly marked as superseded once replacement is proven.

### 4.6 Playback requirement

```python
class PlaybackRequirement(StrEnum):
    ANY = "any"
    PAUSED_TRANSACTION = "paused_transaction"
    RUNNING_FOR_PROGRESS = "running_for_progress"
```

This is a declared requirement, not permission for the handler or native monitor to toggle global playback independently. The playback coordinator satisfies and reconciles it.

### 4.7 Operation interaction contract

```python
@dataclass(frozen=True, slots=True)
class OperationInteractionContract:
    interaction_kind: InteractionKind
    recipient_scope: RecipientScope
    selection_dependency: SelectionDependency
    completion_milestone: CompletionMilestone
    conflict_policy: RecipientConflictPolicy
    playback_requirement: PlaybackRequirement
```

`OperationDefinition` should own either a static contract or one resolver:

```python
interaction: OperationInteractionContract | None
resolve_interaction: InteractionContractFactory | None
```

Exactly one must be populated. The resolved contract becomes part of binding/authorization evidence and generated documentation.

Transport and native code receive a resolved contract ID/value and a materialized dispatch basis. They validate consistency; they do not maintain their own command-name exception lists.

### 4.8 Operation result and plan synchronization

Every operation result that can create or observe retained work must carry, in typed form:

- the highest milestone causally reached;
- exact retained command/order references;
- monitor disposition;
- native order disposition when known;
- whether a world outcome was observed, not observed, not applicable, or unknown;
- the recipient IDs and target identity to which the result applies.

The plan-local executor must render and branch on those semantics honestly. “Succeeded” cannot remain an undifferentiated synonym for both “assignment delivered” and “world objective completed.” Existing operation kinds may remain outcome-oriented, become assignment-oriented, or split into two semantic offers, but one operation identity may not change that meaning implicitly.

Post-dispatch monitoring, step conditions, non-progress assessment, and outcome comparison for recipient-bound work must address the captured recipients or command ID. They may not read mutable `selected.*` state after selection is free to change. `selected.*` remains valid only for actual selection/primary UI assertions.

---

## 5. Immutable dispatch basis

Introduce an immutable model similar to:

```python
class DispatchBasis(StrictModel):
    command_id: str
    identity_session_id: str
    control_ownership_generation: int
    based_on_revision: WorldStateRevision
    operation_fingerprint: str
    interaction_contract_fingerprint: str
    interaction_kind: InteractionKind
    recipient_scope: RecipientScope
    intended_recipient_character_ids: tuple[str, ...]
    primary_character_id: str | None
    selected_character_ids: tuple[str, ...]
    active_platoon_id: str | None
    recipient_character_ids: tuple[str, ...]
```

Operation-specific target identity remains in the typed native request, not in this generic model.

### Capture point

The basis must be materialized **after** the input lease is acquired and **after** the final input-boundary observation is validated. Capturing it earlier would preserve the same stale-selection problem under a better type name.

### Required invariants

- IDs are unique and belong to the current roster.
- The operation fingerprint and resolved interaction-contract fingerprint equal the scheduling authorization's identity.
- The dispatch basis is materialized only from `AuthorizationDecision.bound_operation`; no second direct binder call is permitted.
- `PRIMARY` resolves to exactly `primary_character_id`.
- `CURRENT_SELECTION` resolves to the exact canonical selected-character ID set represented by the freshly rebound operation. Tuple ordering is deterministic serialization only; primary identity is carried separately and must never be inferred from tuple position.
- `EXPLICIT_RECIPIENTS` resolves from exact typed action/binding identities, not from incidental current selection.
- `ACTIVE_PLATOON` resolves to exactly the exported active platoon's members.
- Primary, selected set, active platoon, and recipients are all carried even when some are not authoritative, so later evidence can explain what was true at dispatch.
- Native acknowledgement echoes the immutable basis and request fingerprint.
- A changed control-ownership generation invalidates any not-yet-issued delayed continuation.
- No monitor compares the basis with current UI selection after dispatch.

### Dispatching to captured recipients later

Some operations issue more than one native order over time. Map travel's interior leg is the clearest current example. A continuation must target the captured recipients, not whatever happens to be selected later.

Implement one native `ScopedOrderDispatcher` with this preference order:

1. Use a proven API that accepts explicit character handles directly.
2. Otherwise, under exact UI/input ownership, save primary and selection, select the captured recipients, issue the selection-broadcast order, and restore the prior primary and selection.
3. Live-prove that the temporary selection and restoration do not redirect or cancel either the new order or unrelated retained orders.

No operation should hand-roll this mechanism.

---

## 6. Native command registry and recipient lifecycle

Replace `g_activeNativeCommand` with a bounded registry keyed by `command_id`.

The registry owns controller-command storage, exact correlation, at-most-once deduplication, recipient-level lifecycle bookkeeping, and bounded history. It does **not** own operation semantics, decide target-specific success, choose retry policy, define cleanup, or become a native command-name dispatcher. Those meanings remain in `OperationDefinition`, the exact handler, and semantic telemetry interpretation.

A command record should retain:

- immutable dispatch basis;
- command semantic and target identity;
- captured stable recipient handles;
- accepted sequence/time;
- required completion milestone;
- recipient conflict policy;
- per-recipient lifecycle state;
- aggregate command state derived from recipient states;
- causal links such as `superseded_by_command_id` and `supersedes_command_ids`;
- monitor-independent last observed evidence;
- terminal/disposition sequence when known.

### 6.1 Recipient-level state

A group command cannot honestly have one undifferentiated state. One character can arrive while another is pathing; one can be replaced while the rest continue; one resource may have fewer operator slots than selected recipients.

Use a model along these lines:

```text
RecipientOrderState
  recipient_id
  accepted
  activity_observed
  outcome_observed
  current_task_semantic
  disposition
  disposition_reason
  superseded_by_command_id
  last_observed_sequence
```

Disposition vocabulary should distinguish at least:

- `retained`
- `naturally_ended`
- `replaced`
- `explicitly_cleared`
- `recipient_unavailable`
- `unknown`

Do not use generic `cancelled` for native order disposition.

### 6.2 Aggregate state

Aggregate status is derived from the operation's completion policy, not from a universal “all recipients completed” rule.

Examples:

- Group travel may require arrival for all still-owned recipients.
- Dialogue approach may require dialogue opening for one designated primary participant while other approach recipients are merely supporting movement; exact behavior requires live proof.
- Resource production may count actual resource operators and output growth rather than every selected recipient having a matching task.
- A simple assignment operation may finish host-side when all intended recipients have accepted or when the subset Kenshi actually accepted is explicitly reported.

### 6.3 Overlap behavior

Disjoint recipient sets may coexist.

For overlap:

- `REJECT_OVERLAP`: reject before issuing input and report the exact conflicting command/recipient IDs.
- `SUPERSEDE_OWNED_ORDER`: issue the new order, establish causal replacement, and mark only the overlapping recipients of the old command as replaced once replacement semantics are proven.

Queueing is not a policy option. An overlapping order either rejects or
supersedes; there is no third behavior until queue semantics are proven and
implemented together (§4.5).

A group command remains live for unaffected recipients after partial replacement.

### 6.4 Registry bounds

Active/retained commands must never disappear merely because a recent-acknowledgement ring reached capacity.

Keep separate collections:

- currently retained command records, bounded only by an explicit active-command ceiling that rejects overflow rather than evicting causality;
- bounded recent terminal history;
- optionally persisted run evidence for older commands.

If an active ceiling is reached, reject new orders with a typed capacity result rather than evicting live causality.

### 6.5 Ownership provenance and session reset

Each command record must state that it is controller-issued and retain the identity session and control-ownership generation under which it was dispatched. Separately exported Kenshi orders/tasks may be linked to a command only when causal evidence supports that link.

If the telemetry identity session changes, captured native handles and unconfirmed command links are invalid. Mark affected command dispositions `unknown_after_session_reset` or an equivalent typed state; do not adopt them across sessions solely because a character ID, target label, or task semantic happens to match. After reconnect, show controller-owned retained commands, observed-but-unowned world orders, and unknown provenance distinctly.

### 6.6 Idempotent dispatch and ambiguous acknowledgement

`command_id` is an at-most-once dispatch key, not merely a log label.

- Repeating the same command ID with the same request/dispatch fingerprint returns the existing acknowledgement or command record and emits no second order.
- Reusing a command ID with a different fingerprint is rejected.
- A host timeout with ambiguous acknowledgement does not automatically create a new command ID and retry the order. It first queries the exact command record or reports delivery unknown.
- Retry and idempotency policy remain owned by the existing operation definition/kernel path. The native registry supplies deduplication evidence; it does not invent retry policy.

Budget reservation follows causality: reserve before delivery; commit when exact delivery/order acceptance is proven or when delivery is ambiguous but cannot safely be refunded; release only when no input/order was causally issued. Monitor detachment never refunds an accepted assignment. Retained progress observations do not spend additional strategic-action budget.

---

## 7. Separate native order lifecycle from host monitor lifecycle

The current `OptionStatus` vocabulary can remain for host execution, but it must not be treated as the native order's state.

Introduce a monitor disposition or equivalent evidence:

```text
attached
finished_at_required_milestone
detached_timeout
detached_interruption
detached_run_end
detached_supervisor
```

When `OperationMonitor` reaches a timeout or accepts a strategic interruption:

1. Stop awaiting the option.
2. Preserve the native command record.
3. Record monitor detachment and the last known order disposition.
4. Return a typed operation result that says whether the order was retained, replaced, ended, or unknown.
5. Never invoke an underlying clear unless an operation explicitly requested exact clearing.

A Python task may still be cancelled as an implementation mechanism. The user-visible and planner-visible semantic must be “monitor detached,” not “Kenshi order cancelled.”

### Operation terminal semantics

The existing single terminal owner remains. It now resolves against the operation's declared milestone:

- `INPUT_DELIVERED`: terminal after exact delivery evidence.
- `ORDER_ACCEPTED`: terminal only after operation-specific evidence that Kenshi adopted the order for the exact recipients/target; transport acknowledgement alone is insufficient.
- `ACTIVITY_RUNNING`: terminal after matching task/process evidence.
- `WORLD_OUTCOME_OBSERVED`: terminal after operation-specific world evidence.

A retained order may outlive the operation that successfully assigned it. That is expected, not leakage. The operation terminal is emitted exactly once and is immutable. Causally later native-order and world-outcome facts are appended as linked events through `OutcomeRecorder`; they never rewrite the original receipt or plan-step terminal.

### Preemption and handoff matrix

Preserve the Stage 4 separation between operation authority, independent supervision, human ownership, and finalization. The semantic response depends on lifecycle phase:

- **Before input delivery:** abort the operation; no game order exists.
- **Inside a bounded UI transaction:** stop agent input, account for or close only controller-owned UI state, and establish pause through the existing supervision/final-state path.
- **After order acceptance:** detach the monitor, retain the game order, and pause if supervision or handoff requires it.
- **Before a delayed continuation order:** revalidate agent control ownership and the captured ownership generation. Human handoff or F12 suppresses new continuation delivery; zero new agent primitives may occur after human ownership begins.
- **Reflex supersession while agent-owned:** use the same recipient conflict and causal-link rules as planner-issued work.

Rename or replace stale policy vocabulary such as `CANCEL_ON_REFLEX` where the actual post-acceptance behavior is monitor detachment. Host task cancellation may remain an implementation detail; planner and evidence semantics must describe what happened to the Kenshi order.

---

## 8. Playback ownership

Create one narrow runtime `PlaybackCoordinator` or assign equivalent in-run reconciliation to an existing global runtime service. It must be subordinate to `RunCoordinator`; it must not become a second sequencer, planner, operation monitor, or finalizer.

`FinalSafeStateOwner` remains the sole owner that decides, performs through its narrow safety path, and causally confirms terminal pause/cleanup. The playback coordinator may satisfy ordinary in-run pause/running requirements and expose delivery mechanics to the final-state owner, but it may not independently declare the run safe or perform a second final cleanup.

Responsibilities:

- reconcile requested playback requirements across the active UI transaction and retained game orders;
- provide a temporary paused transaction for UI-sensitive operations;
- resume the configured running speed when world progress is required;
- honor safety-supervisor emergency pause;
- honor a terminal-pause request from the sole final-state owner without taking ownership of finalization;
- report which orders remain retained while paused.

Native command monitors:

- never call `togglePause`;
- never treat `world_paused` as cancellation;
- report `suspended_by_pause` or simply preserve accepted state with no progress;
- resume observation when the world runs again.

This stage should not optimize speed selection. Preserve one conservative configured gameplay speed and centralize ownership first.

---

## 9. Telemetry and protocol reconstruction

This is a deliberately breaking semantic change. Perform one protocol bump and update producer, Python models, fixtures, schemas, generated docs, and consumers atomically. Do not retain `squad` as a compatibility alias beside `roster`.

A major bump is justified: telemetry `2.0.0` and native request schema `2.0`, unless the repository has a stricter internal versioning rule that mandates another exact number.

### 9.1 Roster and platoons

Replace the false single-squad model with:

```text
roster: CharacterState[]
platoons: PlatoonState[]
active_platoon_id: string | null
ui.selected_character_id
ui.selected_character_ids
```

`CharacterState` should carry `platoon_id` when known. `PlatoonState` should carry stable ID, display name when available, and member IDs. Validate that platoon memberships refer to roster IDs and do not contradict one another.

This is the lowest-risk part of Slice 2. `kenshi/Platoon.h` is already included
in the plug-in and `ActivePlatoon` is already used for `getPlatoon()` and
`getSquadLeader()` on NPC targets (§2.3) — the API is exercised, it has simply
never been read for player characters. Sequence platoon export ahead of Jobs and
operator capacity, whose SDK support is still unverified.

Capability names should also be corrected: distinguish `roster.*`, `platoons.*`, `selection.*`, and `primary.*` instead of treating all of them as `squad.*`.

### 9.2 Orders and Jobs

Export separately, at the most truthful level the native API supports:

- ordinary current/queued orders;
- Jobs;
- permajobs;
- Jobs-enabled state;
- current task/activity evidence.

Do not infer a Job from mining animation or infer an ordinary order from the Jobs UI. Unknown queue identity or task target remains unknown.

### 9.3 Resource operators

For usable resources, export when available:

- operator capacity;
- current operator IDs;
- ordinary task/activity evidence by operator;
- current output quantities.

This allows a group production command to report partial adoption honestly when more characters were selected than the resource can use.

### 9.4 Native control state

Replace singular `active_command_id` with plural command records. Remove convenience fields that can contradict the registry unless they are generated directly from it.

Telemetry must expose enough information for Python to:

- find an exact retained command by command ID;
- find commands affecting a recipient;
- distinguish active/retained from recent terminal records;
- inspect recipient-level state;
- follow supersession links;
- distinguish outcome from monitor status.

### 9.5 Stable-subject conditions and outcome recording

Audit every terminal factory, condition path, non-progress fingerprint, and outcome comparison that currently reads `selected.*` or compares the “selected character” before and after an action. Once selection can change, those paths can compare different people and manufacture success, failure, or non-progress.

Recipient-bound monitoring and outcome assessment must use exact recipient IDs from `DispatchBasis`, exact target identity, and causally later revisions. `OutcomeRecorder` remains the sole run-level producer and must record assignment acceptance separately from world change. A selection change is mechanical UI state, not proof that the assigned order achieved its objective.

Progress and stall calculations must use those same stable subjects. Track simulation-eligible elapsed time separately from wall-clock time; paused intervals, stale-telemetry intervals, and periods after monitor detachment cannot be counted as proof that a recipient failed to progress. A typed stall terminal/detachment must retain the last order disposition rather than manufacturing cancellation.

### 9.6 Run evidence, affordance menus, and bounded planner projection

Version run evidence separately from the live protocol. Record, at minimum:

- resolved interaction contract and fingerprint;
- immutable dispatch basis;
- native acknowledgement and highest reached milestone;
- monitor disposition and native order disposition;
- retained-order references and causal supersession links;
- exact recipient/target evidence used by the terminal owner.

Also close the Stage 0–8 observability gap: each planner context must leave an auditable record of the affordances actually offered and, for modeled candidates withheld by a gate, the typed reason code. The planner-facing payload may remain bounded, but the run bundle must make it possible to distinguish “the planner ignored the option” from “the controller never offered it.”

Do not make that record a second semantic registry. Generate contract fields from `OperationDefinition`; keep proof-status/evidence annotations in a separate manifest that references operation/subcase IDs and evidence bundles without restating contract semantics. Generated catalogs are outputs, not authorities.

`PlannerContextAssembler` remains the only planner projection owner. Give the model a compact, bounded summary of active platoon, primary, selection, retained orders, Jobs, and per-recipient availability; keep full native history in telemetry/run evidence rather than flooding every planner call.

---

## 10. Operation and affordance migration

### 10.1 Provisional interaction map

This is a starting classification, not permission to assume unproven Kenshi behavior.

| Operation family | Target contract direction |
|---|---|
| `noop`, `wait`, `stop`, advisor/memory/fieldbook | runtime-only, no recipients |
| `pause`, `set_speed` | global UI/playback, no recipients |
| camera rotation/recovery | global UI, no recipients |
| open/dismiss/scroll screen | global UI transaction, no character recipients unless a specific screen proves primary ownership |
| `select_squad_member`, `select_squad_member_exact` | selection mutation targeting explicit character identity |
| inventory equip, purchase, sell, output transfer | primary or explicit-recipient UI transaction; never first-selected roster order |
| move to character, directional move, map travel | current-selection ordinary order unless an action is redesigned to name explicit recipients |
| regroup with squadmate | explicit actor recipient targeting one squadmate, because the action already carries `actor_id` |
| threat response | explicit actor recipient; group behavior remains proof-required rather than inherited from current selection |
| context operate / resource production | current-selection ordinary order with actual-operator reporting |
| first aid context action | current-selection or explicit-recipient ordinary order depending exact native semantics; resolve by semantic, not generic action kind |
| building exit | likely current-selection ordinary order, but mixed-building behavior must be live-proven |
| dialogue approach | likely current-selection movement with a primary-focused dialogue outcome; live proof required |
| `harvest_resource` | one high-level intent with phase-specific private interaction contracts |
| generic visible controls and game bindings | unavailable unless a semantic adapter supplies an exact contract |

### 10.2 Primary resolution

Every primary-focused operation must resolve through `telemetry.ui.selected_character_id` and verify that ID against the roster.

Delete patterns that choose:

```python
next(member for member in telemetry.squad if member.selected)
```

Roster order is not primary identity.

### 10.3 Select All

Do not continue offering “Select whole party” by collecting the whole player roster.

Withhold the affordance until live proof establishes the binding's scope. If proven active-platoon scoped, represent it as a semantic `select_active_platoon` operation whose terminal is the exact active-platoon member selection. Do not expose it as a generic binding.

### 10.4 Generic controls

`activate_visible_control` and `use_game_binding` must not serve as universal gameplay escape hatches.

Retain them only for bounded UI controls whose effect boundary is intentionally generic and harmless to semantic planning, or make them internal mechanics behind semantic operations. Unknown or mode-sensitive controls are not authorable.

Prospect must become a semantic, time-aware operation with process evidence or be withheld. Clicking the button while paused is not successful prospecting. Its scalar resource values must be modeled as area coverage, not deposit counts: a displayed `0` cannot prove that no discrete iron or copper node exists. A deposit-discovery affordance requires spatial panel interpretation or exported deposit-position telemetry; without that evidence, withhold the discovery claim rather than presenting the scalar as absence.

### 10.5 Resource workflow

Preserve `harvest_resource` as one planner-visible intention if that remains the most useful abstraction, but stop imposing one scope on all of its phases.

A coherent private lifecycle is:

1. **Production assignment** — dispatch an ordinary `operate` order to current selection or explicit worker recipients.
2. **Adoption evidence** — observe actual resource operators and capacity; report partial adoption rather than cardinality failure.
3. **Production progress** — run world simulation and observe output growth; monitor may detach without clearing work.
4. **Collection** — perform a primary/explicit-recipient inventory transaction for `actor_id`.
5. **UI cleanup** — close only the exact UI state opened by the composite.
6. **Order disposition** — leave ordinary work retained unless the operation explicitly requested and proved exact clearing.

The actor receiving inventory does not have to be the only production worker. Authorability must not require singleton selection merely because the later transfer phase has one recipient.

Resource cleanup may release only what the composite can prove it created, in the
channel it created it (§3.5). Clearing a Jobs entry on the theory that the
controller's own ordinary order produced it is an ownership claim requiring live
proof (§13.6), not an assumption; broad clearing as incidental monitor cleanup is
forbidden regardless of which API performs it.

---

## 11. Implementation slices

These are dependency-ordered slices within one architectural stage. They are not permission to retain old and new authorities indefinitely. Each slice should be a coherent edit large enough to leave the repository truthful and green.

### Slice 0 — Accepted checkpoint, baseline evidence, interaction catalog, and proof harness

**Goal:** Start from one known accepted architecture and inventory the current behavior before changing it.

- Close Stage 8 on one green commit and create a rollback tag/checkpoint. Do not waive a generator or environment gate merely because Stage 9 is more interesting. Pin or make deterministic the supported generation environment.
- Baseline at `HEAD`, not at `reconstruction-stage-8-accepted`. Work has continued past that tag (§2.4). Re-run the full portable gate at the baseline commit, tag it separately, and record the four post-acceptance commits and the `selected_count` residue as known delta rather than silently inheriting them.
- Record the current commit, Python/dependency/native build environment, protocol/schema hashes, installed DLL hash, canonical live-config hash, public `./dev` workflow, generated artifact hashes, and representative supervised run-bundle IDs.

**Installation rollback.** Slice 2 is a breaking protocol bump with no dual-read
path, and the plug-in is a DLL installed into a game directory that this
repository does not own. Rolling Python back to a checkpoint is therefore not
sufficient to restore a working system. Before Slice 2, record: the installed
`2.0` and pre-`2.0` DLL artifacts and their hashes, the exact reinstall
procedure for each, and which run bundles were recorded under which protocol.
A rollback instruction that names only a git tag is incomplete for every slice
from 2 onward.
- Preserve distilled evidence for exact rebinding, native order dispatch/terminal behavior, human handoff/F12, final pause, restart continuity, and at least one current group-order trace. Evidence of known-wrong behavior is characterization, not a requirement to preserve it.

Produce a generated interaction catalog from the sole operation registry plus a separate proof-status manifest. For every operation and semantic subcase, record:

- interaction kind;
- recipient scope;
- selection dependency;
- required completion milestone;
- conflict policy;
- playback requirement;
- current evidence source;
- proof status: source-proven, unit-proven, live-proven, or withheld.

Also inventory every native command, generic game binding, and visible-control route.

Add failing/xfail proof scenarios that encode the desired A/B concurrency behavior without weakening current tests. Do not yet patch individual cardinality entries. Every temporary xfail introduced for this stage must be resolved, converted into an explicitly withheld capability test, or deleted before closure.

**Deletes:** undocumented assumption that command name implies scope; ad hoc audit notes not represented in a catalog/evidence manifest; any active Stage 8 steering ambiguity.

**Gate:** Every current operation/control route appears exactly once in the generated catalog or is explicitly internal and named; the proof-status manifest cannot restate semantic contract fields; the Stage 8 checkpoint and rollback boundary are recorded.

### Slice 1 — Core interaction vocabulary and sole registry authority

**Goal:** Introduce the new contract model without yet changing native concurrency.

- Add the orthogonal enums and `OperationInteractionContract`.
- Add static or dynamic contract resolution to `OperationDefinition`.
- Resolve contracts through the sole registry.
- Include the resolved contract and intended primary/selection/platoon basis in stable operation identity; a fresh dispatch-time rebind must concern the same identity.
- Generate contract columns in `docs/generated/OPERATION_DEFINITIONS.md`.
- Convert all definitions in one coherent edit.
- Make authorability consult the resolved scope rather than `SelectionRequirement`.
- Delete `SelectionRequirement` immediately; do not retain both models.
- Remove command-name cardinality exception sets from Python models, replacing them with generic dispatch-basis consistency validation.
- Absorb the `selected_count` gate in `map_destination_already_reached` (§2.4). Whether travel remains available at the current location is a recipient-scope question and belongs to the map-travel definition's contract, not to a telemetry helper.
- Name the concrete member set for `SIMULATION_PROCESS`, or delete the member (§4.1).

During this slice, conservative contracts may preserve current runtime behavior where native support has not migrated yet, but the type system must already express the intended distinction.

**Deletes:** `SelectionRequirement` (source-only; it appears in no test); duplicated Python command-name selection fences; the telemetry-helper cardinality gate.

**Gate:** No operation's recipient scope is inferred outside the operation registry/resolver.

### Slice 1b — Monitor disposition vocabulary and evidence versioning

**Goal:** Stop recording fiction before doing the dangerous work, not after.

This is the half of the original Slice 4 that depends on nothing else. Host
option status versus native order disposition, the timeout/interruption/run-end
paths, and evidence versioning need neither plural native commands nor the
protocol bump. Only exact plural command lookup does, and that stays in Slice 4.

The reason to move it forward is evidentiary. Today the system writes
`cancelled` into run bundles for orders it never verified were cleared, so every
bundle is partly fictional on precisely the axis this stage is about. Slices 2
and 3 are the riskiest work in the plan and will be diagnosed from the bundles
they produce. Landing the honest vocabulary first means that diagnosis rests on
records that distinguish "the monitor stopped watching" from "Kenshi cleared the
order."

- Separate host option status from native order disposition as distinct typed vocabularies (§7).
- Change timeout, strategic interruption, run-end, and supervisor paths from semantic cancellation to monitor detachment with a typed reason.
- Introduce versioned lifecycle/receipt fields for monitor disposition, reached milestone, retained-order references, and native order disposition (§3.11).
- Preserve historical bundle readability; do not reinterpret an existing `OptionStatus.CANCELLED` record as though it had always meant detachment.
- Rename `CANCEL_ON_REFLEX` and `CANCEL_ON_REFLEX_OR_PLAN_PATCH` (`core/operation.py:48-49`) where the actual post-acceptance behavior is detachment.

**Deletes:** false `OptionStatus.CANCELLED` interpretations for underlying native orders; cancellation vocabulary on paths that only stop observing.

**Gate:** No host-side path claims a Kenshi order was cancelled without an issued and verified clear action. Historical bundles remain readable under their recorded version.

### Slice 2 — Protocol 2.0 telemetry: roster, platoons, orders, Jobs, operators

**Goal:** Give the controller truthful world state before it changes lifecycle.

- Export roster, platoons, active platoon, primary, and selection distinctly.
- Export ordinary orders, Jobs, permajobs, Jobs-enabled state, and current activity at the proven fidelity.
- Export resource capacity and current operators.
- Replace `squad` throughout Python, schemas, tests, fixtures, scenario tooling, planner context, and docs.
- Introduce plural native command telemetry models even if native command concurrency lands in the next slice.
- Migrate mock and replay adapters to the same interaction-contract, plural retained-order, and result/lifecycle vocabulary. They may simulate external mechanics differently; they may not preserve the old singleton architecture as a test-only shortcut.
- Perform one breaking protocol bump; no dual-read fallback.

**Named temporary state.** This slice deliberately opens the one gap the rest of
the plan forbids: the protocol declares plural command records while the native
producer still holds one global command. This is the right order — telemetry
shape should not be redesigned twice — but it is a dual state and §16.5 requires
it be named with a deadline rather than discovered later.

- Scope: plural command collections carry at most one record until Slice 3.
- Deadline: Slice 3. It may not survive into Slice 4 under any justification.
- Constraint: no consumer may be written against the singleton assumption during the gap. Python reads the collection as a collection from the moment it exists.
- Exit evidence: Slice 3's gate must show more than one record populated from the native side.

**Deletes:** `TelemetrySnapshot.squad`; singular-squad capability semantics; `NativeControlState.active_command_id`.

**Gate:** The full test suite, generated docs, schema export, and native fixture validation pass against only the new protocol.

### Slice 3 — Native captured-recipient command registry

**Goal:** Make plural disjoint Kenshi orders possible.

- Replace `g_activeNativeCommand` with the command registry.
- Capture stable handles for each recipient at dispatch.
- Monitor captured handles, not current selection.
- Implement recipient-level lifecycle and aggregate status.
- Permit disjoint recipient commands concurrently.
- Implement typed overlap handling and causal supersession.
- Separate active records from bounded recent terminal history.
- Remove `selection_mismatch` from post-dispatch monitoring.
- Stop treating pause as cancellation.
- Remove all monitor-owned pause toggles.
- Implement the one `ScopedOrderDispatcher` for delayed continuation orders such as map travel's interior leg.
- Make command IDs idempotent at-most-once keys and define ambiguous-ack lookup behavior.
- Stamp command records with identity-session and control-ownership generations; suppress delayed continuation after handoff or session change.
- Distinguish controller-caused supersession from external/unattributed task replacement.

**Deletes:** `g_activeNativeCommand`; `command_already_active` as a global lock; monitor-time current-selection checks; native monitor playback mutation.

**Gate:** Native unit/harness tests prove A/B and C/D commands coexist, current selection can change, and a delayed continuation still targets original recipients.

### Slice 4 — Python dispatch basis and plural order lookup

**Goal:** Align transport, options, and operation monitoring with plural native orders.

Slice 1b already landed the disposition vocabulary and evidence versioning. This
slice is what genuinely requires plural native commands to exist first.

- Add immutable `DispatchBasis` captured at the final input boundary.
- Send resolved contract and basis through native request/acknowledgement.
- Replace `_active_native_order_for()` (`execution/handlers/kenshi_surface.py:1034`) with exact plural command lookup independent of current selection.
- Allow host reattachment to a retained controller-issued order only by exact identity-session ID, command ID, and request/operation fingerprint. Recipient/target/task similarity may support diagnostics, but it never establishes ownership.
- Retire the remaining singular `active_command_id` consumers (`condition_evaluation.py:184`, `observation_budget.py:534`, `options.py:544`, and the `planner_context`/`live_dev` readers).
- Preserve one serialized logical input command in `WorldStateStore`; document that it is input causality, not the set of active Kenshi orders.
- Return retained-order evidence, highest reached milestone, monitor disposition, native order disposition, and exact recipient/target identity in operation receipts and run summaries.
- Keep each operation terminal immutable and exactly once; append later retained-order/world-outcome events through `OutcomeRecorder` instead of rewriting the operation receipt or plan step.
- Update plan-step, interrupt-policy, condition, non-progress, and outcome semantics so assignment acceptance is not rendered as world-goal completion and post-dispatch logic never follows mutable selection.

**Deletes:** current-selection equality in order reattachment; singular native command lookup.

**Gate:** Host tests prove that cancelling a monitor does not mutate or relabel the native order, and another operation can dispatch to disjoint recipients afterward.

### Slice 5 — Operation binders, authority, and affordance scope

**Goal:** Make every planner-visible action bind to the proper scope.

- Update binders and input-boundary revalidation to use resolved interaction contracts.
- Resolve primary through `ui.selected_character_id` only.
- Convert explicit-actor actions such as regroup and threat response to explicit-recipient dispatch.
- Make selection-broadcast assignment operations group-aware.
- Ensure selection mutation is modeled separately from commanding the current selection.
- Replace whole-roster “Select All” with a withheld or proven active-platoon semantic action.
- Remove generic control offers that lack a semantic contract.
- Update planner context through `PlannerContextAssembler` to show a bounded summary of active platoon, primary, selected group, retained orders, Jobs, and per-recipient availability.
- Record the actual offered affordance set and typed withholding reasons in run evidence without creating a second handwritten contract registry.

**Deletes:** first-selected actor resolution; entire-roster-as-current-squad behavior; generic scope inferred from UI mechanism.

**Gate:** Generated affordance completeness checks show that every emitted offer has a resolved interaction contract and exact evidence source.

### Slice 6 — Resource lifecycle and phase-scoped composites

**Goal:** Correct the most concrete order/Job mismatch and demonstrate phase-scoped interaction.

- Rework resource production around operator capacity/current operators, or withhold the capability if the SDK does not expose them (§2.3).
- Separate ordinary order, Job, and permajob telemetry and mutation.
- Resolve the existing release path (§2.2, §3.5). Either prove by live evidence that the controller-issued ordinary order created the Jobs entry it clears — which authorizes the current `removeJob()`/`clearOrders()`/`halt()` sequence as ownership-scoped release — or narrow it to what the command can show it owns. Do not delete it reflexively, and do not keep it on the strength of the comment alone.
- Give each private harvest phase its own typed interaction descriptor and milestone under the one parent operation identity; do not recursively invoke the execution kernel or count phases as strategic actions.
- Permit multiple workers while retaining one explicit collection recipient.
- Make resource production able to recognize and plan around existing world activity. Reattach a host monitor only to an exactly proven controller-issued command; pre-existing or externally issued mining remains observed-but-unowned.
- Treat an empty Jobs list during ordinary mining as valid rather than contradictory.
- Report retained production orders when the composite ends or detaches.

**Deletes:** singleton authorability inherited from the collection phase; unowned Job mutation; whole-queue clearing; any cleanup whose breadth exceeds its proven ownership.

**Gate:** Live proof shows one or more characters mining through an ordinary order, empty Jobs where applicable, correct operator telemetry, bounded output, conserved transfer, and no collateral order/Job deletion.

### Slice 7 — Semantic controls and playback integration

**Goal:** Finish the game-model correction around global controls and simulation-backed processes.

- Route all ordinary in-run pause/speed behavior through the sole playback coordinator while keeping `FinalSafeStateOwner` as the sole terminal-pause decision/confirmation owner.
- Make UI-sensitive operations request paused transactions rather than toggling pause locally.
- Make long-running order/process operations request running progress without claiming ownership of speed.
- Introduce progress-aware stall accounting for captured recipients/targets. Paused, stale, detached, and non-agent-ownership intervals do not consume gameplay-progress time; a stall detaches or terminates monitoring with a typed reason and never implies order clearing.
- Replace Prospect with a semantic process operation only if its time/evidence lifecycle is proven. Treat scalar values as area coverage and require spatial deposit evidence for node-discovery claims; otherwise remove the corresponding affordance from offers.
- Audit raw bindings and visible controls; retain only proven semantic adapters or deliberately narrow UI-delivery actions.

**Deletes:** per-command pause cleanup; generic Prospect activation; unknown raw gameplay bindings offered as intentions.

**Gate:** Two groups continue independent work while the planner performs unrelated selection and UI operations; final safety pause preserves and reports retained orders.

### Slice 8 — Live proof, deletion audit, and closure

**Goal:** Prove the new model and make regression difficult.

Run all structural, unit, integration, native, protocol, generated-doc, and supervised live gates. The portable matrix must explicitly include Ruff, mypy, schema/doc staleness checks, mock single-cycle and continuous scheduling through the same coordinator, representative replay, restart continuity, human-handoff tests, import/cycle checks, and absence ratchets. Repeat the public `./dev launch`, `./dev run`, `./dev recover`, and `./dev stop` operator path. Delete transitional helpers, old fixtures, compatibility vocabulary, and stale documentation.

Run the exact vertical proofs before any autonomy soak. After they pass, run a moderate 30–50 strategic-selection soak to expose lifecycle accumulation, selection churn, registry bounds, and restart/reporting defects. Broader gameplay competence is not part of this stage.

Before closure, perform a cold source audit against every slice exit criterion and name any residue explicitly. The Stage 2–4 journey showed that a headline owner can be deleted while a boundary leak survives. Do not let a later type/protocol slice fossilize named residue; either close it or state a bounded withheld behavior that does not contradict the architecture.

Produce a final closure report containing:

- generated interaction catalog and separate proof-status manifest;
- portable, native, replay/restart, live-proof, and soak evidence IDs;
- protocol and persisted-evidence version summary;
- deleted files, authorities, symbols, and compatibility paths;
- net source/test line change and dependency/ratchet results;
- deliberately withheld behaviors;
- remaining frontier work that is not required for stage completion.

**Gate:** Every completion condition in Section 14 passes on one commit.

---

## 12. Required tests and structural ratchets

Add structural tests that fail if the old model grows back.

At minimum, assert:

- `SelectionRequirement` is absent from production code.
- `g_activeNativeCommand` is absent.
- singular native `active_command_id` is absent from protocol models and telemetry output.
- no native monitor terminates because current UI selection differs from dispatch selection.
- `selection_mismatch` appears only in dispatch-time validation, if retained at all.
- no native command monitor toggles pause or speed.
- order/Job/permajob mutation occurs only where a recorded, live-proven ownership claim covers the state being mutated; no cleanup path mutates state it merely observed. (Do not write this ratchet as a ban on `removeJob()` or `clearOrders()` by name — the defect is unowned mutation, not the call. A name-based ratchet would forbid proven release and could be satisfied by renaming a wrapper.)
- affordance code does not resolve primary with “first selected roster member.”
- player roster is not serialized under a `squad` field.
- every operation definition resolves exactly one interaction contract.
- transport/native parsing contains no command-name set whose purpose is independent selection cardinality authority.
- every native accepted order carries immutable recipient IDs and captured handles.
- active command records cannot be evicted by the recent-terminal history bound.
- partial recipient supersession preserves unaffected recipient states.
- delayed continuation orders use the captured dispatch basis.
- monitor detachment leaves native disposition unchanged.
- generated operation docs include interaction kind, recipient scope, selection dependency, completion milestone, conflict policy, and playback requirement.
- the resolved interaction contract and intended recipient basis participate in stable operation identity and dispatch-time same-operation checks.
- `DispatchBasis` is materialized only from the freshly authorized rebound operation; no second binder/guard exists at transport or native edges.
- no recipient-bound post-dispatch terminal, plan condition, non-progress check, or outcome comparison reads mutable `selected.*` state.
- assignment acceptance and world-outcome achievement have distinct typed/result semantics and distinct rendered evidence.
- duplicate native request IDs are idempotent for an identical fingerprint and rejected for a conflicting fingerprint.
- identity-session changes invalidate captured handles and unconfirmed command ownership links.
- controller command records are not presented as the complete set of Kenshi world orders.
- delayed continuation delivery requires current agent ownership and emits no input after human handoff/F12.
- `FinalSafeStateOwner` remains the sole terminal-pause decision and confirmation owner; the playback coordinator contains no run loop, planner call, operation dispatch, or finalization verdict.
- composite handlers do not recursively invoke `ExecutionKernel` or create a second operation registry.
- the native command registry contains no command-name semantic dispatcher for milestones, cleanup, or retry policy.
- operation terminals are emitted once and never rewritten; later retained-order/world-outcome facts are append-only linked events from `OutcomeRecorder`.
- paused/stale/detached intervals do not count as recipient gameplay non-progress.
- mock and replay use the same interaction-contract and lifecycle/result vocabulary rather than a singleton test-only path.
- interaction catalogs derive semantic fields from current registries; evidence/proof manifests cannot duplicate those fields as handwritten authority.
- persisted lifecycle records have explicit versions and historical bundles remain readable or explicitly migrated.

Keep the existing reconstruction ratchets: one coordinator, one binding authority, exact handler coverage, acyclic production graph, bounded environment/execution entry points, direct defining-module imports, an empty core convenience barrel, and a tooling-only generation/proof harness.

---

## 13. Supervised live proof matrix

Each proof must capture pre-dispatch telemetry, immutable dispatch basis, native acknowledgement, later telemetry, and final order disposition. Merely watching characters move is not enough for a protocol claim.

### 13.1 Disjoint group concurrency

1. Select A and B.
2. Order A/B to travel or move.
3. Select C.
4. Order C to a different destination or resource.
5. Prove A/B retained their original order and C accepted the second order.
6. Change selection again and prove neither order was relabelled or cancelled.

### 13.2 Mining plus independent movement

1. Order A to operate a resource.
2. Confirm ordinary order/activity evidence; allow Jobs list to remain empty if that is Kenshi's true state.
3. Select B and order movement.
4. Prove mining and movement coexist.
5. Prove resource operator IDs/capacity are truthful.

### 13.3 Delayed map-travel continuation

1. Dispatch map travel to A/B.
2. Select C before the interior leg is issued.
3. Prove the interior-leg order reaches A/B only.
4. Prove C's current order and selection remain intact after any temporary selection restoration.

### 13.4 Same-recipient supersession

1. Give A an ordinary movement order.
2. Give A a different ordinary order.
3. Prove the old recipient state is linked to the new command as replaced/superseded rather than generically cancelled.
4. For a prior A/B order, supersede B only and prove A retains the original command.

### 13.5 Monitor detachment

1. Dispatch a long-running order.
2. Trigger step timeout, strategic interruption, run end, and supervisor cancellation in separate trials.
3. Prove the host monitor detaches.
4. Prove the underlying order is retained or reported unknown; never falsely “cancelled.”
5. Prove final pause suspends without clearing.

### 13.6 Exact clearing, only if supported

1. Establish ordinary order, Job, permajob, and unrelated queued-order state.
2. Invoke an exact clear operation only after the API's scope is proven.
3. Prove only the intended order/recipient was cleared.
4. Prove Jobs, permajobs, and unrelated queued orders were untouched.

This proof also discharges the standing ownership claim in the resource release
path (§2.2). Establish a pre-existing Job on the actor, dispatch controller-owned
production, release it, and prove that the pre-existing Job survived and that the
cleared Jobs entry was the one the controller's own order created. If that does
not hold, the release is broader than its ownership and Slice 6 must narrow it.

If exact clearing cannot be proven, this proof is replaced by a closure statement that no exact-clear operation is exposed.

### 13.7 Primary-focused UI

1. Create a multi-selection whose roster order differs from primary.
2. Perform inventory/trade/output collection for the primary or explicit actor.
3. Prove the correct inventory changes and the first selected roster entry is irrelevant.

### 13.8 Platoons and Select All

1. Create multiple platoons.
2. Export stable membership and active platoon.
3. Test Select All.
4. Offer a semantic active-platoon selection action only if live evidence proves that scope.

### 13.9 Remaining targeted behavior proofs

- Group dialogue approach and which character becomes the dialogue participant.
- Mixed-building group exit behavior.
- Threat-response recipient scope and whether engage/withdraw broadcasts naturally.
- Resource operator adoption when selection exceeds capacity.
- Temporary selection save/restore for explicit-recipient dispatch.
- Stable platoon identity across tab changes and save/load.
- Prospect process timing, area-coverage scalar semantics, and spatial deposit-location evidence.

Unknown results do not block the whole stage when the corresponding affordance can be honestly withheld. They do block claiming support for that behavior.

### 13.10 Human handoff during retained and delayed work

1. Dispatch a retained order with a delayed continuation path.
2. Trigger human input or F12 before the continuation issues.
3. Prove the monitor detaches and the game is causally paused.
4. Prove no delayed controller primitive/order is emitted after human ownership begins.
5. Prove the existing Kenshi order is retained, externally changed, or unknown without being falsely cleared.

### 13.11 Duplicate request and ambiguous acknowledgement

1. Dispatch one exact command ID/request fingerprint.
2. Repeat it and prove no second game order is issued.
3. Reuse the ID with a changed fingerprint and prove rejection.
4. Simulate host acknowledgement loss and prove lookup/recovery does not issue a fresh duplicate command.

### 13.12 Restart and identity-session reset

1. Retain one or more orders and restart/reconnect the Python process while the native identity session remains stable.
2. Prove exact owned command records can be recovered without reissuing orders.
3. Force a game/plugin identity-session change.
4. Prove captured handles and unconfirmed links become unknown rather than being adopted by coincidental character/task matches.
5. Prove observed-but-unowned world orders remain visible to the planner as such.

### 13.13 Assignment versus achievement evidence

1. Assign travel or production and stop the host operation at `ORDER_ACCEPTED` or `ACTIVITY_RUNNING`.
2. Prove the plan/action record says assignment accepted, not destination reached or output produced.
3. Change selection and continue unrelated work.
4. Later observe the exact recipient/target world outcome and prove it receives a distinct append-only causal record without rewriting the original operation terminal.
5. Prove the run bundle records the actual offer menu and any typed withholding reasons that governed the decision.

### 13.14 Progress-aware stall and pause accounting

1. Dispatch a monitored movement/dialogue approach to captured recipients.
2. Observe real progress, then pause the world and hold it paused longer than the normal stall threshold.
3. Prove paused time does not count as gameplay non-progress and the order remains retained/suspended.
4. Resume and create a genuine recipient/target progress stall under fresh telemetry.
5. Prove the monitor ends or detaches with a typed stall reason while the underlying Kenshi order is retained, ended, replaced, or unknown—not falsely cleared.

### 13.15 Prospect truthfulness, only if exposed

1. Open and complete the semantic prospecting process while simulation is allowed to run.
2. Prove scalar resource values are recorded as area coverage, not deposit counts.
3. In a case where a discrete deposit is spatially visible while its scalar is `0`, prove the controller does not infer absence.
4. Offer deposit-discovery/selection only when spatial panel interpretation or exported deposit positions provide exact evidence.

If spatial evidence is unavailable, this proof is replaced by a closure statement that deposit-discovery affordances remain withheld.

---

## 14. Unavoidable completion state

These conditions are of two kinds, and conflating them is how a stage like this
stalls indefinitely on a gameplay unknown.

**Structural conditions** must be *true*. They assert that the old model is
absent, that each concept has one owner, and that the system does not claim
knowledge it lacks. None of them depends on how Kenshi turns out to behave. An
unproven gameplay semantic never excuses a structural miss; it is discharged by
withholding the capability, which is itself a structural act.

**Capability conditions** — items 6, 17, 18, and 36, each marked below — may be
satisfied *either* by shipping the capability *or* by withholding it with an
explicit statement in the closure report. They depend on live proofs whose
outcome is genuinely unknown today, including whether the SDK exposes operator
capacity at all (§2.3). Withholding is a real pass, not a deferral, provided
§14.31 records it and no advertised capability contradicts it.

Everything not marked is structural.

This architecture stage is complete only when **all** of the following are true on one commit:

1. Stage 8 is green on its supported deterministic environment, accepted on one commit, and recorded as the rollback checkpoint for this stage.
2. `SelectionRequirement` and all duplicated command-cardinality exception lists are gone.
3. Every planner-visible and internal operation resolves one typed interaction contract from the sole operation registry.
4. Primary, selection, active platoon, platoon memberships, and overall roster are distinct first-class telemetry concepts.
5. Ordinary orders, Jobs, permajobs, and Jobs-enabled state are not conflated.
6. *(capability)* Resource operator capacity and current operators are exported where the source supports them, or the capability is withheld rather than guessed.
7. The native bridge supports more than one retained command on disjoint recipient sets.
8. Selecting another character after dispatch does not terminate or redirect an existing order.
9. Delayed continuation orders target the immutable captured recipients.
10. Partial recipient overlap is rejected or causally superseded according to the operation contract; unaffected recipients continue.
11. Host monitor timeout/interruption/run end does not claim to cancel or clear the Kenshi order.
12. A paused world suspends work and never produces `world_paused` cancellation.
13. Individual command monitors never alter global pause or speed.
14. Final run safety may pause the game while accurately reporting retained orders.
15. Every order/Job/permajob mutation is covered by a recorded, live-proven ownership claim; no cleanup path mutates state it merely observed, and no cleanup is broader than its proven ownership.
16. Primary-focused affordances use `ui.selected_character_id`, not roster iteration order.
17. *(capability)* Whole-roster “Select All” behavior is gone; a semantic active-platoon action exists only if proven. Removal of the whole-roster behavior is structural; offering a replacement is the capability.
18. *(capability)* Prospect and other simulation-backed controls are semantic and time-aware or withheld.
19. Generic visible controls/game bindings cannot bypass interaction-contract authority.
20. Protocol 2.0 has one producer/consumer path with no compatibility alias or dual semantic model.
21. Structural ratchets, full Python tests, native tests/build, generated docs, protocol fixtures, and supervised live proofs pass.
22. Assignment acceptance, activity running, and world-outcome achievement remain distinct in operation results, plan records, planner feedback, outcome records, and continuity evidence.
23. No recipient-bound monitoring or outcome logic follows mutable selection after dispatch.
24. Resolved interaction contract and intended recipient basis are part of stable operation identity, and the final dispatch basis is materialized from the one freshly authorized rebound operation.
25. Duplicate command delivery is at-most-once and ambiguous acknowledgement cannot create a second order.
26. Human handoff/F12 suppresses delayed continuation delivery and emits zero new agent primitives after ownership changes.
27. Identity-session reset invalidates captured handles and preserves the distinction between controller-owned and observed-but-unowned world orders.
28. `FinalSafeStateOwner` remains the sole terminal-pause decision/confirmation owner; the playback coordinator is not a second finalizer or sequencer.
29. Composite phase-scoping does not create nested kernel execution, a second registry, or inflated strategic action counts.
30. Run evidence is explicitly versioned, historical bundles remain readable or explicitly migrated, and the actual offered/withheld affordance menu is auditable.
31. The closure report explicitly lists any withheld behavior, and none of those omissions contradicts an advertised capability or operation definition.
32. Each operation terminal is immutable and exactly once; later retained-order and world-outcome facts are append-only, causally linked records from the sole outcome producer.
33. The native command registry remains a correlation/lifecycle component rather than a second semantic dispatcher.
34. Progress/stall accounting follows captured recipients and simulation-eligible time; paused, stale, detached, or human-owned intervals do not manufacture non-progress.
35. Mock and replay exercise the same interaction-contract, plural-order, and result/lifecycle vocabulary through the existing coordinator/kernel path.
36. *(capability)* Prospect never treats a scalar `0` as proof of no discrete deposit; deposit-discovery behavior is spatially evidenced or explicitly withheld. The prohibition on inferring absence from a `0` scalar is structural and holds even if the affordance is withheld entirely.
37. The exact vertical matrix passes before a moderate 30–50-selection soak, and the soak completes without lifecycle leakage, registry eviction of retained causality, or operator-workflow regression.

The stage is **not** complete when A/B concurrency merely works in one demo while the old singular fields, cancellation vocabulary, or selection-cardinality authorities remain. It is complete when the old model is structurally absent and cannot quietly return.

---

## 15. Non-goals

Do not let this stage expand into adjacent projects.

It does not include:

- simultaneous host keyboard/mouse delivery;
- planner-authored selection choreography or raw key sequences;
- a general-purpose background job scheduler;
- a new run coordinator, nested execution kernel, or parallel Python execution path;
- perfect inference of every Kenshi AI task;
- queueing semantics unless explicitly proven;
- speed-policy optimization;
- autonomous multi-platoon grand strategy;
- support for unknown editor/build/camera/squad bindings;
- redesign of inventory conservation, stable identity, display leases, or stale-evidence rejection that already align with Kenshi;
- speculative clearing APIs;
- frontier gameplay features unrelated to interaction scope and order lifecycle.

---

## 16. Agent operating directive

Execute this as a broad architectural correction, not a chain of one-line permission changes.

Begin from the Slice 0 baseline commit, which is `HEAD` and not the Stage 8
acceptance tag — work has continued past that tag and §2.4 records the delta.
Treat each slice as a rollback checkpoint, not as an excuse to preserve both
models. From Slice 2 onward a rollback checkpoint means a git commit *and* the
matching plug-in artifact; a tag alone will not restore a working system. At slice exit, perform a cold source audit against the stated deletion and ownership criteria; name any residue immediately instead of allowing a later slice to fossilize it.

For each slice:

1. Name the old authority being deleted.
2. Introduce the replacement authority in its final intended owner.
3. Migrate all production callers in the same slice.
4. Update tests and generated documentation.
5. Delete compatibility paths before declaring the slice complete. A temporary fallback may own only explicitly unmigrated cases and must have a deletion deadline no later than the next slice; no request may be executed by both paths.
6. Run focused tests while editing, then the full gate at slice end.
7. Record source-proven, test-proven, live-proven, and withheld semantics separately.
8. Report the surviving owner, exact symbols deleted, generated artifacts changed, and whether persistent evidence/protocol versions changed.

Do not preserve a known-wrong abstraction because many tests currently encode it. Tests that enforce singleton selection or monitor-time selection identity are reconstruction targets, not product requirements.

Do not broaden every operation to group scope. Scope follows the gameplay intention:

- global controls remain global;
- primary UI remains primary;
- explicit actors remain explicit;
- selection-broadcast orders capture the current group at dispatch;
- active-platoon operations use the active platoon only;
- simulation processes expose real time and outcome milestones.

The stage should end with a system that can naturally do this:

```text
Select group A.
Assign group A to mine.
Select group B.
Send group B across town.
Open the primary character's inventory.
Observe both retained orders continuing.
Revise or detach from one monitor without erasing either order.
Pause the world safely and report exactly what remains assigned.
```

That behavior is the practical demonstration. The completion gates above are what prove it is architectural rather than accidental.
