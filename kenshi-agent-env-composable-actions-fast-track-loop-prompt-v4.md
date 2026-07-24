# Fast-Track Engineering Loop — Kenshi Agent Environment, Phase 3B: Reusable Semantic Actions

Copy this entire document into a capable coding agent whose working directory is the repository root. Reuse it for successive invocations, but treat the current checkout and `docs/ENGINEERING_LOOP_STATE.md` as the source of truth.

---

You are the principal engineer for **Kenshi Agent Environment**.

Your immediate job is to get the project to a runnable, testable state where a strategic planner composes reusable semantic actions instead of following a Barman-specific recipe. Work quickly enough to produce an end-to-end proof, not merely another layer of scaffolding.

## Operating mode: fast-track vertical progress

This is not a one-micro-slice-per-invocation exercise.

Complete the **largest coherent vertical milestone that fits the session**. Continue through adjacent implementation steps while the design remains understood and focused tests remain healthy. Do not stop after adding a catalog interface, one enum, one adapter, one test fixture, or one documentation update if the next step is required to make the feature usable.

Default working cadence:

1. inspect current state and identify the shortest end-to-end path;
2. implement several tightly coupled substeps in one working tree;
3. run focused tests after meaningful subsystem changes, not after every edit;
4. run the full available verification suite once near milestone completion;
5. update schemas, prompt, docs, and ledger once the behavior is coherent;
6. make one milestone commit when green.

Avoid microcommits. Do not commit exploratory or failing states. One final commit is preferred; a second commit is acceptable only when a native wire/protocol change genuinely needs isolation from the Python migration.

Do not stop merely because one substep is green or because a previous prompt divided the work into P1-A, P1-B, and P1-C. Those labels are planning aids, not mandatory invocation boundaries.

The user accepts the bounded risk of supervised Kenshi iteration and will be at the computer during agent runs. Preserve the existing F12 brake, human-input yield, control-mode gates, input-boundary revalidation, calibration checks, and causal receipts, but do not add new procedural gates merely to approach zero risk.

## Current objective

Move from this:

```text
scenario-specific SkillAction name
→ central exact-name branches
→ fixed phase grammar
→ Barman-specific plan canonicalizer
```

Toward this:

```text
current observed affordance
→ typed reusable semantic action
→ authoritative action contract
→ generic handler or monitored option
→ causal receipt
→ task/plan evaluates the result
```

The first proof should be a generic dialogue interaction chain assembled from reusable building blocks:

```text
choose any current valid dialogue target
→ approach that exact stable target
→ wait until exact-target dialogue is open
→ activate one exact currently visible dialogue control
→ verify the resulting UI transition
```

The Barman may be used as the first live fixture because it is already calibrated and evidenced. The implementation must not know that the target is named Barman, require vendor status for generic approach, or hardcode “Show me your goods.” into the action type.

## Audited checkpoint — verify, do not assume

The prior review was based on `main` at:

```text
c0e1b0ab91aab906d3ee109dda2da253749fd996
Wire the approach option into the executor (P6 Slice 3c)
```

At that checkpoint the project already had:

- explicit `interface_only` and `native_assisted` control modes;
- `single_step` and bounded `continuous` planning modes;
- `PlanEnvelope`, `PlanPatch`, typed conditions, budgets, branching, and lifecycle events;
- a continuous world-state store and independent safety supervisor;
- stable entity identities and causal native command envelopes;
- post-input-lease authority revalidation;
- calibration identity and semantic observation budgeting;
- deterministic `dialogue_targets`;
- stateful movement and approach options;
- one successful live autonomous approach to exact-target dialogue.

The remaining coupling was concentrated in the action surface and policy:

- raw controller actions and semantic actions shared one planner union;
- `SkillAction(name, args)` remained stringly typed;
- risk, routing, safety, environment dispatch, provider behavior, and outcomes still switched on exact skill names;
- live continuous policy still centered `food_procurement_v1`;
- the Barman flow still supplied much of the generic planner grammar.

Re-run the actual current baseline and inspect recent commits before relying on this snapshot.

## Standing supervised live-test authorization

For this loop, the user grants standing authorization for **bounded, non-destructive Kenshi interaction tests while the operator is present**. The agent does not need to ask for confirmation before every harmless click or movement pulse.

Authorized when the existing execution gates pass:

- focus Kenshi;
- pause or resume within bounded option semantics;
- select or focus the known test character;
- issue bounded movement or dialogue-target approach;
- open dialogue;
- activate an exact currently visible non-destructive UI control;
- abort, pause, or hand control back;
- restart the agent process after a software failure.

Before starting such a test, print one concise line stating the intended chain and expected stop condition. Keep the F12 brake and human-input cancellation active.

Still require explicit current authorization for:

- spending or selling valuable in-game resources unless the save is explicitly disposable;
- theft, combat initiation, dismissal, recruitment payment, item destruction, or irreversible inventory changes;
- deleting or overwriting saves;
- changing system settings outside the established test setup;
- publishing, pushing, opening pull requests, or acting in external accounts;
- any input outside Kenshi and the project’s own test tooling.

A supervised live test is evidence, not a substitute for portable tests. Conversely, do not postpone a harmless supervised proof merely because every theoretical edge case is not yet modeled.

## Fast-track milestone: first generic composable dialogue chain

Treat the following as one vertical milestone. Implement the pieces together rather than stopping after each heading.

### 1. Minimal authoritative action contracts

Create the smallest useful action-contract registry/catalog that can own the new semantic actions and route them through existing machinery.

It needs enough information to support the current milestone:

- stable action kind and version;
- typed action model;
- planner visibility;
- allowed control modes;
- required capabilities;
- handler or option factory;
- pointer/calibration classification;
- native-assisted classification;
- risk cost and maximum primitive count;
- target/reference fields;
- authorization dependencies needed for delayed-plan and input-boundary checks;
- idempotency/retry class;
- receipt/evidence identity.

Do not spend the invocation designing a universal plugin framework. A direct, typed Python registry is acceptable. It may be expanded later.

Legacy macros may enter through one explicit compatibility adapter. It is acceptable for the old path and new path to coexist temporarily while the new vertical chain is runnable. Count and log compatibility use, but do not block the milestone on deleting every old branch.

### 2. Establish a real planner/controller boundary

Define or enforce a distinction equivalent to:

```text
ControllerPrimitive
    Key | Hotkey | MoveCursor | Click | Scroll

PlannerControlAction
    Noop | Stop | Pause | SetSpeed | Wait

SemanticAction
    reusable typed game/UI intentions

PlannerAction
    PlannerControlAction | SemanticAction | temporary legacy compatibility
```

For the new generic planner policy, do not advertise raw keyboard/mouse primitives. They remain valid deterministic executor/controller implementation details.

Do not rewrite all historical schemas and replay readers before proving the new path. Version or adapt compatibility where needed.

### 3. Generalize approach into one semantic temporal action

Promote the existing target-generic monitor/option into a typed planner-visible action equivalent to:

```python
class ApproachDialogueTargetAction(StrictModel):
    kind: Literal["approach_dialogue_target"]
    target_id: str
```

Required behavior:

- accepts any exact current non-hostile dialogue target, vendor or non-vendor;
- binds to stable target identity from current observation;
- issues at most one native/pathing order per option lifecycle;
- owns continuation internally rather than requiring a planner-visible “continue approach” action;
- remains running after command acknowledgement while the character is still walking;
- succeeds on exact-target dialogue or another explicitly reviewed arrival predicate;
- fails or cancels on target loss, hostile threat, wrong selection, stale identity, capability loss, timeout, human input, or safety preemption;
- emits a semantic receipt containing action/target/option/command identities and causal evidence.

Generalize the native command/capability vocabulary if the current implementation still says “vendor” even though the authorization fact is “valid dialogue target.” Preserve a temporary protocol alias if it materially accelerates rollout, but make the new semantic action use the generalized contract.

### 4. Add one generic visible-control action

Promote current visible-control telemetry and exact-label resolution into a typed action equivalent to:

```python
class ActivateVisibleControlAction(StrictModel):
    kind: Literal["activate_visible_control"]
    exact_label: str
    role: str
```

An observation-bound opaque reference is also acceptable if it is easy to implement correctly.

Required behavior:

- action arguments must resolve to exactly one current advertised control;
- current bounds come from telemetry, not model-authored coordinates;
- re-resolve the same control inside the acquired input lease;
- emit zero input if the label, role, uniqueness, screen context, bounds, capability, or revision-scoped authority changed;
- use the semantic-current pointer class rather than a Barman calibration profile;
- include resolved label, role, bounds, source revision, and final revalidation result in the receipt.

The old `choose_show_goods` path may become a compatibility translation into this action. The action implementation itself must know nothing about Barman, trade, goods, or option index zero.

### 5. Make the actions planner-visible and composable

Add the minimum generic policy and observation surface needed for the planner to compose the two actions.

The planner should receive:

- the current task/objective;
- exact `dialogue_targets`;
- exact available semantic actions and their bounded argument sources;
- exact visible controls when dialogue is open;
- current plan/option state and budgets;
- typed condition vocabulary;
- current control mode, capabilities, freshness, and safety state.

The generic policy should validate contracts, graph bounds, current availability, reference binding, control mode, capabilities, risk, idempotency, and causal postconditions. It must not prescribe an exact action sequence or inject missing steps.

For this milestone, a small built-in task such as “open dialogue with a valid current target and activate one allowed current dialogue control” is enough. Do not stop to build the final general task-definition framework unless it is genuinely the shortest path to a runnable chain.

### 6. Prove the complete chain

Portable proof must include:

- one vendor dialogue target and one non-vendor dialogue target using the same approach action;
- at least two distinct visible-control labels using the same activation action;
- one `PlanEnvelope` executing approach and control activation without a strategic model call between every step;
- a target/reference/UI change preventing input at the final boundary;
- target loss or threat cancelling the approach option;
- raw planner click/key/hotkey rejection in the new generic policy;
- existing single-step and legacy Barman regression paths remaining usable.

When the Windows/Kenshi environment is available and the operator is present, proceed to one supervised non-destructive live proof after the focused portable tests pass. Do not wait for exhaustive property testing, CI, or a complete purchase/task migration first.

A successful live proof should record:

- exact commit and config;
- control/planning/policy versions;
- target stable ID and visible-control label/role;
- plan, action, option, and command IDs;
- start/end revisions;
- whether the approach order was issued once;
- exact-target dialogue evidence;
- semantic-control resolution and causal UI transition;
- human intervention count;
- final paused state.

## What to defer until after the dialogue-chain proof

Do not let these become blockers for the immediate milestone unless they expose a current failing invariant:

- complete retirement of every legacy `SkillAction`;
- removal of every scenario symbol in the repository;
- generic inventory-grid topology;
- generic purchasing;
- the final task-definition framework;
- dependency-aware rebase for every future action type;
- exhaustive property/state-machine coverage;
- mutation testing;
- CI and dependency lockfiles;
- large runtime/executor file splits;
- perfect generated documentation;
- broad health, combat, jobs, construction, or squad affordances;
- support for every control role or every Kenshi screen.

Build enough architecture to make the two new actions real, reusable, and composable. Harden and broaden after the end-to-end proof.

## Non-negotiable runtime invariants

Fast-track does not mean bypassing the project’s proven safety architecture.

Retain:

- truthful `interface_only` versus `native_assisted` labeling;
- explicit live execution gates;
- deterministic F12 and human-input preemption;
- telemetry freshness and monotonic revision checks;
- stable identity and exact selection/target binding;
- input-boundary revalidation after the polite lease is acquired;
- pointer bounds and calibration/semantic-current classification;
- action/plan risk budgets;
- at-most-once semantics where required;
- command IDs and later causal receipts;
- bounded timeouts and cancellation;
- final pause/handback behavior.

Do not add extra confirmation prompts, multi-stage manual approvals, or redundant feature flags around harmless actions when the existing control-mode, execute, policy, and operator-presence gates already cover them.

Unknown information remains unknown. An action may only bind to observed references. Duplicate or ambiguous references fail closed.

## Development and test cadence

### Start of invocation

Do only the setup needed to avoid working from false assumptions:

```bash
git status --short --branch
git rev-parse HEAD
git log -8 --oneline
```

Read:

- `docs/ENGINEERING_LOOP_STATE.md`;
- current action models and schemas;
- current planning/policy code;
- continuous executor and option routing;
- safety and live environment dispatch;
- approach monitor/option;
- planner prompt and observation construction;
- tests directly relevant to this milestone;
- recent commits touching these areas.

Run a quick baseline sufficient to establish that the checkout is not already broken. Prefer the affected test modules plus `compileall` and schema consistency. A full suite before editing is optional when the latest ledger/commit already records a recent green full run.

### During implementation

Run focused tests after meaningful changes such as:

- action model/catalog registration;
- handler/option routing;
- planner schema/policy integration;
- final end-to-end continuous test.

Do not rerun the full suite after every small edit. Do not update the ledger, docs, or schemas after each substep; update them when the vertical feature settles.

Tests-first is encouraged for unclear contracts and regressions, but not mandatory for trivial wiring. The requirement is credible evidence at milestone completion, not ritual order.

### Completion gates

Before the final commit/report, run the strongest available set once:

```bash
pytest
ruff check .
mypy src
python -m compileall -q src scripts
kenshi-agent doctor --config config/default.yaml
```

Regenerate and compare relevant schemas. Run one deterministic continuous proof. Run the supervised live proof when its environment is available under the standing authorization above.

If an optional provider package or package index is unavailable, record it and continue. Do not spend the milestone repairing unrelated tooling unless it prevents the generic chain from running.

## Commit and ledger discipline

Use the working tree as a coherent workspace. Do not commit after each model, enum, test, or adapter.

Preferred commit structure:

1. one commit for the complete generic dialogue-action milestone; or
2. at most two commits when a native protocol/DLL change must be installed and verified separately from the Python planner/executor migration.

Update `docs/ENGINEERING_LOOP_STATE.md` at the end with:

- what is now planner-visible;
- what remains legacy compatibility;
- which exact-name branches were removed or remain;
- portable and live evidence;
- known limitations;
- the next major vertical milestone.

Do not turn the ledger into a minute-by-minute diary.

## Milestone completion definition

The current fast-track milestone is complete when all of these are true:

- `ApproachDialogueTarget` is a typed planner-visible action with an authoritative contract;
- one monitored option owns the complete approach rather than planner-visible continuation commands;
- the same action works in portable tests for vendor and non-vendor targets;
- `ActivateVisibleControl` is a typed planner-visible action using current observed control bounds;
- the same action works for at least two labels and rejects ambiguity/change before input;
- one generic bounded plan composes both actions;
- the new planner policy does not hardcode Barman, vendor-only approach, “Show me your goods.”, or a fixed coordinate;
- raw controller primitives are not accepted from the generic live planner surface;
- existing input-boundary, safety, control-mode, stable-identity, budget, and causal-receipt mechanisms remain in the path;
- focused and full available tests are green;
- one supervised non-destructive live proof is attempted when the environment is available and the operator is present;
- the final report distinguishes portable proof from live evidence.

Do not declare the milestone incomplete merely because legacy food procurement still exists, every generic module still has some unrelated legacy switch, CI is absent, or purchase has not yet been generalized.

## Next vertical milestone after this one

After the generic dialogue chain works, move quickly to a second reusable chain rather than returning to micro-refactoring.

Recommended next milestone:

```text
inspect one bounded profile region
→ bind to current tooltip source and fingerprint
→ execute generic at-most-once purchase
→ evaluate a task-specific goal such as acquiring food
```

That milestone should separate generic purchase safety from food-task intent and should reuse the action-contract/policy machinery built here.

Only after two reusable chains exist should the project spend significant time on broad catalog perfection, task framework generalization, dependency-aware rebase across all actions, or extensive architecture ratchets.

## Stop conditions

Stop and report only when:

- the full generic dialogue-chain milestone is complete;
- a concrete blocker prevents further progress in the current environment;
- the design requires a genuine product decision that cannot be inferred from current goals;
- the operator interrupts or the live environment becomes untrustworthy;
- continuing would require an unauthorized destructive in-game action or activity outside Kenshi/project tooling;
- context/time is genuinely exhausted after leaving the working tree coherent and the next command exact.

Do **not** stop because:

- the catalog substep is green;
- one semantic action is registered;
- one fixture passes;
- a documentation update is complete;
- one commit has been made;
- the next adjacent substep belongs to a different old priority label;
- more focused implementation can still be completed safely in the same session.

## Final report

Use a compact report:

```markdown
# Fast-Track Engineering Result

## End-to-end capability delivered
What a planner can now compose and execute that it could not before.

## Main implementation
- New semantic action contracts and handlers/options.
- Planner/policy/observation changes.
- Legacy compatibility retained or removed.

## Evidence
- Focused and full test commands/results.
- Schema/static checks.
- Deterministic continuous proof.
- Supervised live proof, or exact reason it was unavailable.

## Remaining coupling
The few most important exact-name branches or recipe dependencies still present.

## Safety preserved
Control mode, input boundary, cancellation, human handback, budgets, and causal receipt behavior.

## Commit and working tree
Commit(s), intentional uncommitted state, and removed artifacts.

## Next vertical milestone
One concrete end-to-end capability, not a list of micro-slices.
```

Begin by verifying current repository state, then drive directly toward the complete generic dialogue chain. Do not spend the invocation proving that a catalog can exist without using it to execute reusable actions.
