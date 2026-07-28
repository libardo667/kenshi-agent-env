# Engineering loop — harden evidence-grounded continuity, add the fieldbook, and prove long-horizon play

Copy this entire document into a capable coding agent whose working directory is
the `kenshi-agent-env` repository root. Reuse the same prompt for successive
invocations.

This prompt **supersedes the earlier memory-and-continuity loop prompt**. The
first major authority slice has already landed in the reviewed checkout: campaign
scope, runtime-owned action and plan outcomes, explicit memory lifecycle
operations, append-only history plus a rebuildable projection, migration, and
read-only recall are real code now. Do not restart that work or replace it with a
new parallel framework. Verify the current checkout because later commits may
have advanced beyond the snapshot described here.

One invocation completes **one compound vertical slice**, leaves the tree green,
and makes one intentional commit. Do not spend an invocation merely renaming a
field, drafting an ADR, adding unused abstractions, or fixing one example file
while a related invariant remains broken. Conversely, do not mix unrelated
renderer, movement, combat, or native-protocol work into this feature.

Work with high agency. Inspect, decide, implement, test, document, and commit.
Do not stop for naming approval when this prompt already establishes the policy.
Do not turn ordinary in-game consequences into a reason for procedural paralysis;
existing live-input authority remains in force, but most work in the early slices
is portable and requires no live input at all.

## Mission

Finish a continuity system that lets a Kenshi-playing agent distinguish:

1. what the game and controller actually proved;
2. what it recently attempted and what happened;
3. what it deliberately chose to remember;
4. which commitments and unresolved questions remain open;
5. what larger bodies of work it may deliberately reopen later;
6. which exact pieces of context were actually shown to the planner that authored
   a continuity operation.

The result is not “increase the memory limit.” It is a truthful, inspectable
boundary among **world evidence**, **working continuity**, **durable kept
memory**, and a private **fieldbook**.

The feature is complete when an agent such as Ladle can pursue a delivery over
many plans and real process restarts, retain grounded route lessons, maintain a
bounded delivery docket and route atlas, correct or supersede old beliefs, see
why a continuity operation was rejected, and still treat current Kenshi
telemetry as authoritative when its own notes disagree.

This is not a generic cognitive-architecture rewrite. Do not import affective
substrates, simulated needs, souls, pulses, reveries, or WorldWeaver's broad
resident runtime. Strengthen the existing Kenshi planner, executor, observation,
logging, and evaluation architecture.

## Operating posture

- Treat the current checkout, `git log`, `STATUS.md`, `CHANGELOG.md`, generated
  schemas, tests, and run evidence as the source of truth.
- Preserve working behavior unless a migration or contract change is explicitly
  required by the invariants below.
- Fix classes of defects, not one reproduction. If one continuity path can bind
  to a later observation, inspect plan, decision, patch, rebase, advisor, replay,
  and subprocess paths for the same mistake.
- Prefer a complete vertical invariant across models, runtime, store, planner
  adapters, observation budgeting, schema, tests, metrics, and documentation over
  a locally elegant but unused class.
- Keep one canonical authority. A new side file, cache, Markdown export, or
  embedding index must never become an independent source of memory truth.
- Do not store or request private chain-of-thought. Persist concise explicit
  facts, episodes, commitments, hypotheses, decisions, observations, questions,
  and project notes only.
- Use coding agents aggressively, but remain accountable for the semantics. A
  passing test that proves the wrong contract is not progress.

## Definition of complete

The overall feature is complete only when all of the following are true:

- every planner-authored continuity operation is paired with the exact planner
  context that made its references available;
- `current_observation` can never silently rebind from the planner-visible state
  to a later commit-time state;
- a planner can cite only IDs actually present in its delivered context, not any
  plausible ID that happens to exist elsewhere in the run or database;
- evidence is classified by what it can establish, not merely by whether its ID
  exists;
- no-op, unknown, not-executed, advice-only, memory-only, or stale evidence can
  establish a successful world-effect fact or close a world-effect commitment;
- commitment and hypothesis resolution requires explicit closure evidence;
- expected storage conflicts become typed continuity receipts and never escape as
  raw SQLite errors or partially applied transitions;
- evicted working outcomes retain enough immutable digest data to remain
  auditable and, when deliberately resurfaced, citable;
- canonical memory history retains structured references and resolved evidence
  snapshots, not only a flattened prose grounding string;
- delivery diagnostics name only the memories and other continuity records that
  were actually included in the final planner input;
- recent continuity receipts are visible to the next planner under a bounded
  policy;
- all checked-in examples, prompts, configs, schemas, docs, and metrics use the
  current contract;
- a structured fieldbook exists with bounded automatic indexing and elective
  reads, without becoming physical Kenshi inventory;
- deterministic recall and search work without embeddings;
- optional compaction and semantic retrieval are explicit, logged treatments;
- a restart-spanning Ladle evaluation demonstrates continuity behavior without
  overstating general gameplay competence.

## Implemented foundation to verify and preserve

Inspect the checkout before acting. In the reviewed post-first-slice snapshot,
the following foundation exists and should be treated as protected unless a
specific defect below requires an evolution:

### Campaign scope

- `src/kenshi_agent/campaign.py` resolves an explicit configured campaign,
  explicit ephemeral run, deterministic scenario/save campaign, or mock/replay
  run scope.
- A live run with durable memory and no defensible campaign identity fails
  closed instead of sharing a global default namespace.
- Legacy rows migrate into `legacy:<namespace>` rather than being assigned to
  whichever save opens the database first.

### Runtime-owned working continuity

- `ContinuityLedger` issues stable run-local `ao-...` action-outcome IDs and
  `po-...` plan-outcome IDs.
- `ActionOutcome` carries plan, step, command, assessment, revisions, and
  feedback.
- `PlanOutcome` retains the original objective, disposition, completed steps,
  terminal reason, and terminal revision.

### One continuity authority

- `ContinuityAuthority` is the route from planner-authored continuity operations
  into durable memory.
- The planner uses explicit `keep`, `reinforce`, `resolve`, `supersede`, and
  `retract` operations rather than free-text `memory_writes`.
- Facts and episodes currently require at least one reference; commitments and
  hypotheses may be authored as intention or uncertainty.
- Exact target-bound writes require an ID from fresh observed entities.
- Every operation produces an accepted, rejected, or no-op receipt and is logged.

### Versioned canonical store

- `memory_events` is append-only history.
- `memories` is a rebuildable projection updated transactionally with history.
- Recall is read-only and no longer refreshes the ranking merely because the
  observation pump runs.
- Exact restatements reinforce by deterministic normalized key.
- Closed records refuse later transitions.
- Migration backs up the legacy database, preserves old rows as
  `legacy_unverified`, and is intended to be idempotent.
- `kenshi-agent memory` provides read-only operator inspection.

### Better commit timing

- Accepted plans process continuity only after plan validation.
- Single-step decisions process continuity after their action receipt.
- Plan patches process continuity only when the exact patch is actually applied.
- Rejected, stale, discarded, or superseded patches contribute no durable
  continuity.
- The automatic durable `Set out to: ...` episode has been removed; plan purpose
  now lives in runtime-owned plan outcomes.

### Existing boundaries to keep

- Exact entity-scoped recall never reactivates by display name or fuzzy match.
- Stale telemetry provides no current target IDs.
- Current-target memories receive protected observation-budget treatment.
- Current telemetry, current references, action contracts, input-lease
  revalidation, and controller receipts remain authoritative over memory.
- Generated schemas and hosted planner contracts are strict and must remain
  provider-portable.

If any of these are absent in the actual checkout, classify them as regression or
partial completion. Repair them in dependency order rather than building later
features atop a missing foundation.

## Current reviewed defects — reproduce before fixing

The following issues were observed in the reviewed snapshot. Do not trust the
summary blindly: first add or run a focused reproduction against the current
checkout. If a later commit already fixed one, cite the code and test proving it
and move to the next incomplete dependency.

### P0. Planner-visible observation can be rebound at commit time

`CurrentObservationEvidence` currently contains only the literal source name.
`render_evidence_reference()` renders whichever `Observation` is passed when the
operation commits.

In single-step mode, the planner authors the operation from the pre-action
observation, but the runtime applies it after dispatch using the post-action
observation. A planner-visible telemetry/frame revision of `1/1` can therefore be
stored as `current_observation(2/2)`, even though the planner never saw revision
`2/2` when it wrote the claim.

The same class of bug can occur around rebase, concurrent patch planning,
advisor latency, observation-pump advancement, and any future delayed sidecar.

Required invariant:

> A continuity operation resolves `current_observation`, exact target IDs, and
> every advertised evidence ID against the immutable planner context from which
> that exact output was authored. Commit-time state may decide whether the
> operation is still applicable, but it may never silently substitute itself as
> the source.

A runtime-stamped authored context is preferable to trusting the model to copy a
revision correctly. An explicit revision field is acceptable only when the
runtime also captures and validates the actual authored basis.

### P0. Issued IDs are not the same as delivered IDs

The current authority checks whether an action outcome was ever issued in the
run, whether a memory exists in the campaign, and whether an advisor brief was
ever issued. That is weaker than the planner contract.

A planner must be able to cite only references that were actually delivered in
the context used for that planner call. Sequential IDs are guessable, and a
stored memory ID or old output ID may exist without being present in the current
prompt.

Required invariant:

> Every planner response is paired with a runtime-owned manifest of the exact
> continuity IDs and exact world revision included in its final input. Evidence
> resolution checks that manifest, not merely global existence.

An elective read may deliberately place an older memory, outcome, or project
entry into a later manifest. Without such a read, an evicted or omitted record is
not silently citable.

### P0. Delivery is currently recorded before final budgeting

`AgentRuntime._decide()` currently marks every `observation.memories` record as
“delivered” before hosted planner adapters call `Observation.planner_payload()`.
Observation budgeting may then omit some or all general memories.

A small budget can produce a final hosted payload containing zero memories while
the database records every recalled memory as delivered.

Required invariant:

> “Delivered” means included in the exact final planner input submitted to or
> consumed by that planner implementation. It does not mean present on an
> unbudgeted `Observation` object.

This must work across OpenAI, OpenRouter, subprocess, scripted, heuristic, replay,
and future planner adapters. Different planner types may legitimately consume
different representations, but each must produce an honest context manifest.

### P0. A commitment can be resolved without evidence

`ResolveMemoryOperation.references` currently defaults to an empty list, and the
authority accepts an unsupported reason such as `Delivered.`. Because plan-level
continuity is processed before plan execution, a plan can close a commitment in
the same response that merely proposes to satisfy it.

Required invariant:

- `resolve` always requires explicit references;
- only record kinds whose lifecycle can meaningfully close are resolvable;
- commitments and unresolved hypotheses/questions may resolve;
- facts and episodes are corrected by supersession or retraction, not marked
  “resolved” as though they were tasks;
- a world-effect commitment requires at least one closure-capable item of world
  evidence already available in the authored planner context;
- plan acceptance, a free-text reason, advice, another belief, or a no-op cannot
  close it.

### P0. Evidence existence is checked, but evidence capability is not

The current runtime can accept a fact or episode grounded only by:

- an advisor brief;
- another memory, including a hypothesis;
- a commitment;
- a no-op action outcome;
- an unknown or not-executed action outcome;
- a plan outcome whose plan ended but whose world objective was not causally
  established;
- or an evicted outcome rendered only as `evicted`.

A generic runtime cannot prove arbitrary natural-language entailment. Do not add
an LLM “truth judge” and pretend it solves this. The runtime can, however,
classify evidence by what it is structurally capable of establishing and reject
obviously invalid combinations.

Required invariant:

> Evidence references resolve to typed immutable snapshots with explicit
> authority/capability, not immediately to strings. Operation validation uses
> those capabilities before rendering a human-readable summary.

The minimum evidence policy is specified below.

### P0. Evicted outcomes retain identity but lose meaningful provenance

`ContinuityLedger` retains sets of issued IDs after full outcomes leave the
visible window. Later rendering becomes:

```text
action_outcome(ao-1: evicted)
```

That proves only that an ID once existed. It loses action kind, assessment,
execution status, semantic terminal, target, and revisions—the very information
needed to decide whether it can ground anything.

Required invariant:

> Full recent outcomes may be bounded, but every issued outcome retains a compact
> immutable evidence digest for the lifetime of the run. Eviction removes rich
> display detail, not authority metadata.

The digest must remain bounded per record and should not copy screenshots or full
observations. Session logs may preserve the full record.

### P0. Canonical memory history flattens structured evidence

Continuity receipts contain typed references at application time, but the memory
store primarily persists a rendered grounding string. If session logs disappear,
the canonical memory event history cannot reconstruct which exact structured
references, assessments, semantic statuses, planner context, and revisions
produced the record.

Required invariant:

> Canonical lifecycle history stores the planner-authored operation, authored
> context identity, exact structured references, runtime-resolved evidence
> snapshots, origin, plan/step provenance, and a rendered summary. The prose
> summary is a projection for humans, not the sole durable provenance.

A projection rebuild must reproduce the same current memory state and preserve
all source links.

### P0. An expected uniqueness conflict can escape as raw SQLite failure

A reproducible sequence is:

1. keep active memory A;
2. keep active memory B;
3. supersede A with replacement content whose normalized active key equals B.

The unique active-memory index raises `sqlite3.IntegrityError`. The transaction
rolls back, but the error escapes `ContinuityAuthority` because only
`MemoryTransitionError` is handled.

Required invariant:

> Expected semantic/storage conflicts become typed rejected continuity receipts,
> leave both event history and projection unchanged, and do not invalidate an
> otherwise valid game action or plan.

Do not indiscriminately swallow database corruption. Distinguish expected
constraint/transition conflicts from unexpected store failure. An unexpected
failure must roll back, produce explicit diagnostics, quarantine or disable
continuity for the run if necessary, and never masquerade as a normal rejection.

### P1. Continuity receipts are logged but invisible to the planner

The next planner cannot see that its previous operation was rejected, accepted as
a reinforcement, or changed a memory ID. A deterministic invalid operation may
therefore repeat.

Required invariant:

- every receipt receives a runtime-owned ID;
- a bounded recent receipt digest reaches the next planner;
- at least the latest rejected/failed receipt survives observation budgeting;
- receipts are working continuity, not durable memory;
- a receipt grants no game-action authority;
- planner guidance explains how to respond to rejection instead of blindly
  repeating it.

### P1. Contract and repository hygiene drift remains

The reviewed checkout contains small but real drift:

- `prompts/planner_system.md` shows a numeric memory ID in an example even though
  the schema requires a string `mem-...` ID;
- checked-in JSONL examples still emit removed `memory_writes` fields and fail
  strict current parsing;
- comments and some metrics retain old terminology;
- `config/live.longform.yaml` is described as generic but hardcodes
  `campaign_id: ladle-css-01`, allowing an unrelated save opened with that
  profile to inherit Ladle's continuity;
- documentation includes stale test-count claims;
- there is no repository-wide test loading every checked-in planner example
  against its current declared output model.

Fix these in the same slice as the contract they belong to. Do not spend a whole
invocation on cosmetic drift alone.

## Authority model

The following layers are mandatory and must stay distinct.

### 1. World evidence

Telemetry, screenshots, world-state revisions, current exact references, action
receipts, controller-owned semantic evidence, native acknowledgements, scenario
attestations, and runtime-assessed outcomes are the only evidence that can
establish game state or game effects.

World evidence answers questions such as:

- Is cargo currently visible in a complete inventory export?
- Did money increase?
- Did this exact command receive a causally later terminal acknowledgement?
- Is this exact entity currently present in fresh telemetry?
- Did source quantity fall while destination quantity rose by the same amount?

### 2. Working continuity

Working continuity is bounded, recent, runtime-owned, and primarily run-scoped.
It includes:

- action outcomes;
- plan outcomes;
- continuity operation receipts;
- elective memory/fieldbook read receipts;
- the planner-context manifest associated with each planner output.

It says what was attempted, what was shown, what changed, and why work ended. It
is not durable belief.

### 3. Durable kept memory

Durable memory is campaign-scoped, agent-authored continuity: facts, episodes,
commitments, and hypotheses that may affect later decisions. Every active record
has explicit lifecycle and structured provenance. It remains secondary to current
world evidence.

### 4. Private fieldbook

The fieldbook is a larger campaign-scoped workspace for named continuing bodies
of work: delivery dockets, route atlases, incident logs, vendor notes, equipment
plans, and other projects.

Ordinary observations contain only a bounded project index and perhaps one
explicitly selected active project summary. Full entries are available only
through an elective bounded read.

The fieldbook is not Kenshi inventory. A note saying “six slop canisters” cannot
create, preserve, transfer, sell, or deliver six in-game items.

## Non-negotiable invariants

1. **Current world evidence wins.** Memory and fieldbook may guide inquiry but
   never override fresh telemetry, current exact references, controller receipts,
   or safety state.
2. **Authored context is immutable.** Continuity references resolve against the
   exact context delivered to the planner that authored them, never whichever
   observation happens to exist later.
3. **Delivered means actually delivered.** An ID is citable only when the final
   planner input manifest says it was included, or a later elective read placed
   it into a new manifest.
4. **Continuity grants no action authority.** A remembered target ID, cell label,
   coordinate, window, key, or capability never authorizes a later game action.
5. **No future success enters memory.** A plan cannot cite or store the success
   of its own future steps. Those runtime-owned IDs do not exist yet.
6. **Epistemic kinds remain distinct.** Commitment is intention; hypothesis is
   uncertainty; fact is an agent-authored claim grounded in world-capable
   evidence; episode records an observed event or attempt and preserves its
   failure/unknown status.
7. **Evidence capability matters.** An existing ID is not automatically adequate
   proof. Advice, beliefs, no-ops, unknowns, and procedural completion retain
   their limits.
8. **Resolution is earned.** A commitment or hypothesis closes only with explicit
   already-delivered closure evidence. A reason string is not evidence.
9. **Entity identity stays exact and lifetime-bounded.** Names, roles, positions,
   and similarity never reactivate an entity-bound memory.
10. **Campaigns do not bleed.** Unrelated saves, fixtures, characters, and tests
    never share private continuity merely because they use the same config file.
11. **Recall is not reinforcement.** Reading, prompting, budgeting, and
    observation decoration cannot increase a memory's importance.
12. **Automatic context is bounded.** Exact current-target constraints and open
    commitments may receive protected space. General memories, receipts, and
    project indexes remain bounded.
13. **One canonical continuity authority.** No JSONL side store, Markdown export,
    embedding cache, or session log may independently inject durable state.
14. **Unknown stays unknown.** Incomplete inventory, stale telemetry, missing
    outcome detail, failed reads, and ambiguous references do not become absence,
    loss, success, or certainty.
15. **No opaque forgetting.** Records may leave automatic recall, but deletion,
    semantic rewriting, resolution, supersession, and retraction are explicit.
16. **Embeddings are optional retrieval infrastructure.** They never decide
    whether a memory may be stored or whether a claim is true.
17. **Continuity failure is isolated.** A semantically invalid sidecar operation
    receives a typed receipt and does not cancel otherwise valid gameplay.
    Unexpected store failure remains explicit and cannot partially apply.
18. **Structured provenance survives.** Human-readable grounding is derived from
    structured canonical evidence, not the other way around.
19. **No hidden reasoning persistence.** Do not store private chain-of-thought or
    ask models to reveal it.
20. **Tests prove behavior.** Avoid source-text assertions where an executable
    contract can be tested.

## Planner-context authority

Introduce one runtime-owned concept representing exactly what a planner call
received. Exact names may differ; the semantics may not.

A useful shape is:

```python
class PlannerContextManifest:
    context_id: str
    run_id: str
    authored_revision: WorldStateRevision
    telemetry_was_fresh: bool
    action_outcome_ids: tuple[str, ...]
    plan_outcome_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]
    advisor_brief_ids: tuple[str, ...]
    continuity_receipt_ids: tuple[str, ...]
    fieldbook_project_ids: tuple[str, ...]
    fieldbook_read_receipt_ids: tuple[str, ...]
    created_at: datetime
```

The exact fields may evolve, but the manifest must satisfy these rules:

- it is runtime-owned and immutable;
- it corresponds to one planner invocation;
- it records the exact world revision and exact continuity IDs in the final input;
- the planner output is paired with that manifest through parsing, validation,
  rebase, execution, and sidecar application;
- `current_observation` resolves to `authored_revision` from this manifest;
- exact target IDs are validated against the fresh authored observation, not a
  later commit observation;
- evidence IDs must appear in this manifest or in a typed read result included by
  this manifest;
- commit-time observation may be recorded separately for audit but cannot replace
  the authored basis;
- a stale authored observation cannot ground a fresh-current-state fact;
- the manifest itself is working history and may be logged without becoming
  durable memory.

### Honest payload assembly

Do not continue marking delivery in `AgentRuntime._decide()` before planner
adapters budget or serialize their input.

Create one authoritative preparation seam that yields both:

1. the final representation consumed by that planner; and
2. the manifest of records actually included.

Possible implementations include a `PreparedPlannerInput`, `PlannerCallContext`,
or planner-returned input receipt. Choose the smallest design that works across
all planners without duplicated semantics.

For hosted text planners, `Observation.planner_payload()` or its replacement
should return the rendered text **and** an inclusion manifest. For in-process
heuristic/scripted planners, the full observation they receive may legitimately
make all attached records delivered. For subprocess planners, be explicit about
whether the process receives full observation JSON or a budgeted representation.

Record delivery only after the final representation exists and immediately before
or as it is handed to the planner. Define the metric precisely as “included in
planner input,” not “provider certainly read every token.” A failed provider call
may still have an input receipt; distinguish attempted submission from a parsed
planner response where useful.

## Typed evidence vocabulary

Resolve each reference into an immutable structured snapshot before validating an
operation. A useful internal shape is:

```python
class ResolvedEvidenceSnapshot:
    source: str
    source_id: str
    authority: EvidenceAuthority
    authored_context_id: str
    run_id: str
    world_revision: WorldStateRevision | None
    assessment: str | None
    action_kind: str | None
    executed: bool | None
    causal_revision_advanced: bool | None
    semantic_status: str | None
    plan_disposition: str | None
    memory_kind: str | None
    memory_status: str | None
    compact_summary: str
```

Do not copy arbitrary whole observations into the memory database. Store the
minimum immutable facts needed to interpret the reference later.

### Evidence authority classes

Use an enum or equally explicit policy. At minimum distinguish:

- `fresh_world_observation` — exact fresh state at an authored revision;
- `verified_world_effect` — controller-owned terminal or causally supported
  observed effect;
- `observed_change` — runtime saw tracked change but may not prove the intended
  goal caused it;
- `attempt_changed` — an action executed and something changed;
- `attempt_no_op` — action executed but no material tracked effect followed;
- `attempt_not_executed` — executor did not perform it;
- `attempt_unknown` — outcome could not be verified;
- `plan_disposition` — the plan ended in a particular way; not automatically a
  world-effect proof;
- `agent_belief` — an existing memory, with its kind and status;
- `advice` — an advisor brief; never direct world evidence;
- `scenario_attestation` — exact fixture identity where applicable, not a claim
  about an action effect.

Exact enum names may differ. Do not collapse them into one boolean `supported`.

### Minimum admissibility matrix

#### Keep or supersede a fact

Requires at least one delivered reference capable of describing already-observed
world state or effect:

- fresh exact current observation;
- controller-verified semantic effect;
- causally later action outcome whose structured status is adequate for the
  claim class.

An advisor brief, existing memory, hypothesis, commitment, plan outcome, no-op,
not-executed outcome, or unknown outcome may supplement context but cannot be the
sole grounding for a new fact.

The runtime cannot prove that arbitrary prose perfectly follows from the source.
Do not pretend otherwise. Preserve the source statuses in the planner-visible
receipt so misleading prose is inspectable.

#### Keep or supersede an episode

Requires an observed event source:

- action outcome;
- plan outcome;
- or fresh current observation where the episode is already visible.

A failed, no-op, or unknown attempt may legitimately ground an episode **about
that attempt**, but its snapshot and rendered grounding must preserve
`no_op`, `not_executed`, or `unknown`. It cannot be normalized into success.

Advice or memory alone cannot establish that an episode happened.

#### Keep a commitment

May be self-authored after the containing decision/plan/patch has itself passed
its applicable acceptance boundary. References are optional because it is an
intention, not a world fact.

A commitment must be specific enough to close or abandon later. Avoid automatic
commitments for every micro-plan; one ongoing objective should be reinforced or
updated rather than multiplied.

#### Keep a hypothesis

May be self-authored with no references. References may explain what prompted it.
It remains explicitly uncertain regardless of source quality until a separate
operation resolves, supersedes, or retracts it.

#### Reinforce

Reinforcement means the agent deliberately chose the record again. It must never
happen because the observation pump recalled the record.

When references are supplied, persist their structured snapshots. Reinforcing a
fact with advice-only or belief-only evidence may increase declared importance
but must not be described as new world confirmation. Consider separate
`salience` and `confidence` if the current model needs that distinction; do not
let one number imply both importance and truth.

#### Resolve

Only active commitments and active hypotheses/questions are resolvable.
`references` is non-empty.

For a commitment involving a world effect—deliver, purchase, earn, recruit,
arrive, transfer, equip, escape—at least one reference must be fresh world state
or adequate world-effect evidence. Advice, memory, a no-op, an unknown attempt,
or plan completion alone cannot close it.

For a hypothesis, resolution must preserve whether the evidence confirmed,
rejected, or left it unknown. If one `resolve` verb cannot express that honestly,
add a bounded typed resolution disposition.

#### Retract

Retraction may remain agent-authored with a reason, because it withdraws a belief
rather than establishing a new world fact. It never deletes history.

#### Supersede

The replacement is validated under the rules of its new kind. The old record and
replacement transition atomically. A conflicting active replacement key produces
a rejected receipt and no state change.

## Working continuity digests

Keep the full recent `ActionOutcome` and `PlanOutcome` windows for planner context,
but retain compact immutable digests for every ID issued during the run.

An action-outcome digest needs at least:

- outcome ID and run ID;
- plan ID/version and step ID;
- action kind;
- exact target/semantic identity when applicable;
- executed flag and assessment;
- command ID;
- action-start and completion revisions;
- controller-owned semantic terminal/status where present;
- short bounded evidence summary;
- recorded timestamp.

A plan-outcome digest needs at least:

- plan outcome ID;
- original objective;
- disposition;
- completed step IDs or count;
- actions completed;
- terminal revision;
- terminal reason digest;
- timestamps.

Do not retain screenshots, full telemetry, or unbounded prose in the digest.
A run with a large action budget should remain reasonable in memory. If an
indexed run-local SQLite table or session-log index is cleaner than an unbounded
Python dictionary, use it, but do not create a second durable belief authority.

Automatic planner context still shows only the bounded recent window. Older
digests become citable only when an explicit bounded read/search puts them into a
new planner context manifest.

## Canonical memory provenance

Evolve the schema version transactionally. The append-only event must retain
structured evidence, not merely `grounding: str`.

For every lifecycle transition, persist at least:

- memory ID and campaign ID;
- lifecycle event and exact planner-authored operation;
- origin: decision, plan, patch, compaction, or operator migration;
- source run ID;
- plan ID/version and step ID where applicable;
- authored planner context ID and authored world revision;
- commit-time world revision where applicable;
- exact planner-authored reference union;
- runtime-resolved evidence snapshots;
- rendered human-readable grounding;
- status/transition result;
- timestamp;
- predecessor/successor links.

The current `memories` projection may retain a bounded latest grounding summary
for recall. The full structured event remains canonical.

Projection rebuild must reproduce:

- lifecycle status;
- content, kind, salience, and target;
- reinforcement count and timestamps;
- resolution reason/disposition;
- supersession links;
- latest delivered timestamp where that diagnostic remains part of projection;
- structured source links needed by operator inspection.

Migration requirements:

- preserve v1 and v2 data;
- back up before destructive schema change or use an equally strong transactional
  migration/rollback path;
- mark old flattened grounding honestly as legacy/unstructured provenance;
- do not invent structured references for old rows;
- make reopening idempotent;
- test projection rebuild after migration.

## Store failure isolation

At the store boundary:

- preflight expected active-key conflicts when practical;
- translate expected `sqlite3.IntegrityError` constraints into
  `MemoryTransitionError` or an equally typed domain rejection;
- guarantee event and projection rollback together;
- preserve both old and conflicting active records after rejection;
- emit a continuity receipt with the exact reason;
- continue otherwise valid gameplay.

For unexpected I/O, corruption, or database failure:

- roll back;
- log a distinct store failure, not an ordinary semantic rejection;
- do not claim that memory changed;
- disable/quarantine further continuity writes for the run when continued writes
  cannot be trusted;
- keep reads only if their integrity is still defensible;
- report the degraded state to the planner/operator;
- do not silently delete or recreate the live database.

Add a distinct `failed` receipt status if accepted/rejected/no-op cannot describe
this honestly.

## Planner-visible continuity receipts

Add a bounded runtime-owned receipt ledger. A receipt digest should include:

- receipt ID;
- operation and origin;
- accepted, rejected, no-op, or failed status;
- reason;
- resulting memory ID/status where any;
- authored context ID/revision;
- plan/step provenance;
- compact evidence summary;
- timestamp.

Observation policy:

- surface a small recent list;
- preserve the latest rejected/failed receipt through budgeting;
- do not surface an unbounded operation history;
- clear nothing merely because it was shown;
- do not rank durable memory by receipt visibility.

Planner guidance must tell the model to correct the exact rejected operation and
not repeat it unchanged. A successful receipt may provide a new `memory_id` for a
later reinforce/resolve/supersede operation.

## Recall and elective search

The current read-only exact-target/general recall is a good base but not the final
policy.

Implement a deterministic default before semantic retrieval. Recommended tier
order:

1. active ongoing commitments relevant to the campaign;
2. exact memories bound to IDs in the fresh authored observation;
3. unresolved high-priority hypotheses or survival constraints;
4. remaining general active records ranked by declared salience, explicit
   reinforcement, lifecycle, and creation/reinforcement time—not delivery time;
5. optional relevance-selected records for any remaining slots.

Do not let one tier consume an unbounded number of slots. Exact-target and open
commitment guarantees should be explicit in observation-budget tests.

Add an elective bounded memory search/read action after the provenance foundation
is correct. It must:

- emit zero keyboard, mouse, or native primitives;
- create no world command;
- spend no pointer, purchase, or native risk budget;
- search only the current campaign unless an operator tool explicitly says
  otherwise;
- return typed result IDs, source metadata, and honest truncation;
- place returned IDs into the next planner-context manifest so they become
  citable;
- never authorize an action by itself.

SQLite FTS5, deterministic token matching, or bounded `LIKE` search is sufficient
for the first implementation. Do not add an embedding dependency merely to ship
search.

## Private fieldbook

Build the fieldbook only after the P0 provenance and failure-isolation defects are
closed.

Use the same campaign scope and one canonical structured store. At minimum
support:

- create project;
- append entry;
- update bounded summary or status;
- set or clear one active project;
- complete, pause, or abandon project;
- inspect one project or search its entries through an elective bounded read.

Useful project types include:

- delivery docket;
- route atlas;
- incident log;
- vendor ledger;
- equipment plan;
- journal;
- generic project.

Project statuses should be explicit: active, paused, completed, abandoned.
Entries carry runtime-owned IDs, timestamps, origin/context provenance, source
references where applicable, and a bounded type such as note, decision,
observation, incident, manifest, route entry, expense, or question.

### Automatic fieldbook context

Ordinary observations expose only a bounded index:

```text
project ID
title
kind
status
short summary
entry count
last update
active-project marker
```

Do not automatically inject the latest prose excerpt from every project. That
creates a self-feedback loop where writing makes the topic more visible, which
causes more writing.

At most one explicitly selected active project may receive a bounded summary in
automatic context. Full entries are elective.

### Fieldbook reads and writes

Reads and writes are cognitive side effects handled by the runtime, not by
`AgentEnvironment` and not as game input.

They must:

- emit zero controller primitives;
- create no Kenshi command;
- have typed receipts;
- respect campaign scope;
- use runtime-owned project/entry IDs rather than arbitrary paths;
- have hard entry and character limits;
- report truncation honestly;
- follow plan/decision/patch commit timing;
- fail independently from gameplay.

A human-readable Markdown export may exist as a disposable generated view.
Deleting or editing that export must not change canonical fieldbook state.

### Physical-world boundary

A fieldbook manifest is not inventory. Current Kenshi telemetry remains the
source of truth for cargo.

Do not add a Python shadow inventory. A future physical “Courier's Ledger” FCS
item may gate access to detailed fieldbook content only as a separate experiment
with fresh complete inventory evidence and, ideally, stable item-instance
identity. It is not required for the first fieldbook slice.

## Compaction

Wire `prompts/memory_compactor.md` only after lifecycle, structured provenance,
planner-context authority, deterministic recall, and fieldbook are complete.
Otherwise keep it clearly inert or remove stale claims that it is active.

Compaction must be explicit and bounded:

- exact source memory IDs are selected;
- all sources belong to one campaign;
- incompatible exact target IDs do not merge;
- incompatible kinds or epistemic statuses do not merge;
- unresolved commitments and unresolved hypotheses are excluded by default;
- the output preserves uncertainty and the weakest relevant confidence;
- the compactor returns a strict candidate, not a direct mutation;
- malformed, truncated, refused, or semantically invalid output changes nothing;
- applying a candidate atomically creates a replacement and supersedes the exact
  sources;
- source history is never deleted;
- provider, model, prompt hash/version, parameters, and source IDs are logged;
- dry-run and operator inspection are supported.

Do not let compaction turn several failed or inconclusive attempts into a durable
success lesson.

## Optional semantic retrieval

Ship deterministic recall and search first. Add semantic retrieval only as an
explicit switchable treatment:

- exact-target and open-commitment tiers remain deterministic and lead;
- candidate pool and top-k are bounded;
- diversity coefficient and minimum relevance are configured and logged;
- provider/model, dimensions, thresholds, and fallback are in run metadata;
- cache keys include memory revision/content hash and provider/model;
- cached vectors are disposable and rebuildable;
- unavailable embeddings fall back honestly to deterministic recall;
- similarity does not suppress storage admission;
- similarity does not prove identity, contradiction, truth, confidence, or
  importance;
- tests use a deterministic fake embedder;
- A/B evaluation compares retrieval policy, not preferred prose style.

WorldWeaver's relevance-plus-diversity logic is design evidence, not code to copy
blindly. Its historical side-store and provider-dependent storage filter are not
the target architecture.

## Dependency-ordered work queue

At the start of every invocation, classify each slice as `absent`, `partial`, or
`complete`, with exact code/tests. Select the **first incomplete dependency**.

Do not skip to the fieldbook because it is more visible. Do not reopen completed
campaign/migration work unless a defect requires it.

### Slice 1 — exact planner-context authority and honest delivery

Complete one end-to-end planner-context manifest and authored-basis path.

Required outcomes:

- every planner output is paired with the exact manifest that produced it;
- `current_observation` resolves to the authored basis, not commit-time state;
- exact target validation uses the fresh authored observation;
- only delivered IDs are citable;
- final budgeting returns an inclusion manifest;
- delivery events match the exact IDs in the final planner input;
- all planner implementations have explicit honest semantics;
- rebase, delayed advisor, patch, single-step, replay, and subprocess paths are
  covered;
- no observation-pump write regression.

Include the straightforward prompt/example/config fixes that directly depend on
this contract, but do not let cosmetic cleanup replace the slice.

### Slice 2 — evidence capability, closure rules, and canonical provenance

Required outcomes:

- references resolve to typed evidence snapshots;
- fact/episode/commitment/hypothesis admissibility follows the matrix above;
- resolve requires closure evidence and applies only to resolvable kinds;
- no-op, unknown, advice-only, belief-only, or procedural completion cannot prove
  successful world effects;
- full recent outcomes plus compact all-run digests exist;
- evicted references retain meaningful assessment and revisions;
- canonical memory events store structured operation/context/evidence provenance;
- projection rebuild and migration preserve it;
- operator inspection can show both structured sources and rendered grounding.

### Slice 3 — transition failure isolation and planner feedback

Required outcomes:

- expected unique-key and transition conflicts return typed rejected receipts;
- unexpected store failure is distinct, rolled back, and explicitly degraded;
- no invalid continuity operation cancels otherwise valid gameplay;
- receipt IDs and bounded receipt digests reach the next planner;
- latest rejected/failed receipt survives budgeting;
- metrics and reports count accepted/rejected/no-op/failed operations accurately;
- all checked-in planner examples parse against current models;
- old `memory_writes` terminology is removed from current outputs while old log
  readers retain deliberate compatibility where needed;
- generic long-form config no longer silently hardcodes Ladle's campaign; add a
  Ladle-specific profile or explicit override path instead;
- prompt examples use real string memory IDs;
- generated schemas/docs and test-count claims are current.

### Slice 4 — deterministic recall, elective memory search, and fieldbook

This may be one large compound slice or two coherent invocations—one for
read/search and one for fieldbook—if the current tree makes that boundary real.
Do not split it into model-only, table-only, and prompt-only micro-slices.

Required outcomes:

- protected open-commitment and exact-target recall tiers;
- deterministic bounded search/read with typed read receipts;
- returned read IDs become citable only in the next manifest;
- campaign-scoped fieldbook projects and append-only entries;
- bounded project index and one active project;
- elective project reads;
- zero game input and zero game-risk spend;
- fieldbook text cannot create or override inventory;
- restart persistence and campaign isolation;
- migration, schema, CLI/operator inspection, metrics, prompt, and docs complete.

### Slice 5 — compaction and optional semantic retrieval

Required outcomes:

- provenance-preserving candidate compaction and atomic application;
- deterministic recall remains default;
- semantic MMR retrieval is optional, explicit, logged, and disposable;
- storage admission remains deterministic;
- provider outage falls back without changing canonical memory;
- controlled tests and A/B metrics exist.

### Slice 6 — Ladle restart evaluation and strongest safe proof

Create a reproducible evaluation around a cargo-delivery campaign. Use synthetic,
mock, replay, fixture-attested, or live evidence at the strongest level the
repository can honestly support.

The evaluation must include at least:

1. a campaign-scoped open commitment to deliver a fixed cargo quantity;
2. multiple plans and action outcomes, including at least one no-op, failed, or
   inconclusive attempt;
3. route or incident details written to the fieldbook rather than all compressed
   into memory;
4. a real process restart using the same campaign ID;
5. the second process receiving the unresolved commitment and bounded project
   index;
6. an elective read of relevant route/delivery material;
7. current telemetry disagreeing with an old note, with telemetry winning;
8. a same-named different entity receiving no old exact-entity memory;
9. a different campaign receiving none of Ladle's continuity;
10. commitment resolution only after cited closure-capable evidence;
11. exact planner-context and delivery manifests in the evidence bundle;
12. continuity rejection feedback causing a corrected next operation rather than
    unchanged repetition.

Compare at least:

- continuity disabled or pre-feature baseline;
- scoped lifecycle memory;
- memory plus fieldbook;
- deterministic versus semantic retrieval, only if semantic retrieval exists.

Measure:

- repeated no-ops;
- resumed commitments;
- stale-memory corrections;
- unsupported success claims;
- cross-campaign leaks;
- evidence-reference rejection rate;
- continuity-operation correction after rejection;
- fieldbook reads and prompt cost;
- exact delivered-memory counts;
- restart continuity;
- eventual delivery status.

Do not use personality resemblance or preferred prose as the success metric.
Do not claim general Kenshi competence from one successful delivery.

After portable and replay evidence is green, run the strongest safe supported
integration proof. A useful live endpoint is the same explicit campaign across
two supported `./dev journey` processes, with the second planner demonstrably
receiving and using a grounded unresolved commitment or route lesson. Existing
live input acknowledgements and human supervision rules remain authoritative.

## Required behavioral tests

Run the current baseline before editing:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

If dependency infrastructure is unavailable, record the exact failure and run the
strongest available focused suite. A package mirror outage is not a green full
baseline.

The following tests, or behaviorally equivalent tests, are required across the
relevant slices.

### Authored context and delivery

- pre-action revision `1/1`, post-action revision `2/2`: a decision's
  `current_observation` grounding remains `1/1`;
- an operation cannot cite an ID issued in the run but absent from its planner
  manifest;
- an operation may cite an older record after an explicit read places it in a
  later manifest;
- stale authored telemetry cannot ground a fresh-state fact or exact target;
- a rebased plan never silently rebases its continuity evidence;
- a staged patch's manifest stays paired with that exact patch through application;
- rejected/discarded patch continuity contributes nothing;
- final payload budgets that include 0, 1, N, and all memories create matching
  delivery events exactly;
- delivery semantics are tested for OpenAI/OpenRouter preparation without live
  provider calls, subprocess, scripted, heuristic, and replay paths;
- planner failure after input preparation is recorded honestly without inventing
  a successful parsed output.

### Evidence capability

- advisor-only fact rejected;
- memory-only fact rejected;
- hypothesis-only fact rejected;
- no-op-only successful fact rejected;
- unknown/not-executed outcome cannot close a commitment;
- plan completion alone cannot prove a world delivery;
- failed/no-op action outcome may ground an episode that remains explicitly
  failed/no-op;
- fresh exact observation may ground a fact about that observation;
- controller-verified transfer evidence may close a transfer commitment;
- commitment keep without world evidence is accepted as intention;
- hypothesis keep without world evidence is accepted as uncertainty;
- resolve with empty references rejected;
- resolve of fact/episode rejected in favor of supersede/retract;
- resolved hypothesis preserves confirmed/rejected/unknown disposition if added;
- evidence IDs from another run/campaign rejected;
- exact target memory never attaches by name or stale identity.

### Working outcome retention

- with action-outcome visible limit 1, `ao-1` retains a compact digest after
  `ao-2` evicts its full record;
- the digest preserves action kind, assessment, execution, semantic status, and
  revisions;
- an explicit read can resurface the digest under a bounded result;
- eviction never changes a `no_op` into generic “exists” evidence;
- large action budgets remain within an explicit memory/performance bound.

### Store and lifecycle

- superseding A with B's active normalized key returns a rejected receipt and
  leaves A and B unchanged;
- event append and projection update roll back together on injected failure;
- an unexpected database failure produces a distinct failed/degraded state;
- closed records refuse invalid transitions;
- cross-campaign IDs are unreachable;
- v1/v2 migration is idempotent and preserves honest legacy provenance;
- projection rebuild reproduces exact current state and structured evidence;
- read-only CLI inspection creates no campaign and writes nothing;
- deleting derived caches or Markdown exports changes no canonical result.

### Receipts and budgets

- every operation receives one receipt ID;
- latest rejected/failed receipt survives a tight observation budget;
- accepted receipt exposes the resulting memory ID;
- the next planner can correct the exact rejected operation;
- receipt visibility does not reinforce durable memory;
- receipt collections remain bounded.

### Fieldbook

- project creation, append, status, active selection, read, pause, complete, and
  abandon round-trip;
- project and entry IDs are runtime-owned;
- campaign isolation and restart persistence;
- automatic context contains index metadata but not every full entry;
- elective read is bounded and reports truncation;
- fieldbook operations emit zero controller primitives and no world command;
- arbitrary paths cannot escape or bypass the structured store;
- Markdown export is disposable;
- a fieldbook manifest saying six items cannot change telemetry inventory;
- incomplete inventory remains unknown rather than lost.

### Repository contract hygiene

- every checked-in JSONL planner example parses against the current strict model;
- generated schemas are fresh;
- planner prompt examples pass a lightweight contract test or fixture parse;
- current configs cannot silently share a named real campaign unless explicitly
  documented as campaign-specific;
- old log/eval compatibility is deliberate and tested rather than accidental.

## Mutation testing

Use mutation testing on the new authority seams, not as a project-wide ritual
that delays the feature.

At minimum kill mutations that would:

- substitute commit-time observation for authored observation;
- accept an issued-but-not-delivered evidence ID;
- mark omitted memories as delivered;
- treat advisor or memory evidence as world evidence;
- allow a no-op to close a commitment;
- allow empty-reference resolution;
- resolve a fact or episode;
- discard an evicted outcome's assessment;
- flatten structured evidence out of canonical history;
- partially append without projection update, or vice versa;
- let a unique-key conflict escape as raw SQLite error;
- hide the latest rejected receipt under budgeting;
- leak another campaign's record or fieldbook project;
- reactivate by display name;
- surface every fieldbook excerpt automatically;
- treat fieldbook prose as inventory;
- delete compaction sources;
- let embedding availability change storage admission.

Kill those mutants or document a genuinely equivalent/non-actionable mutation.
Do not postpone the slice until every unrelated module shard is attended.

## Performance and observation-budget rules

- The observation pump may run around ten times per second. Continuity decoration
  must not perform write transactions at that rate.
- Build the planner-context manifest once per planner call, not per pump tick.
- Add indexes for campaign, lifecycle status, kind, target, project, entry order,
  and deterministic search.
- Bound all automatic collections and elective read results.
- Preserve the latest action/plan outcome, latest rejected/failed continuity
  receipt, open commitments, and exact current-target memories before optional
  general context.
- A deliberately requested read must either fit its documented bound or return
  typed truncation/unavailability. Do not silently drop the chosen source.
- Outcome digests must be compact enough for the maximum supported run budget;
  measure the memory cost rather than assuming.
- Log candidate counts, included IDs, payload characters, and retrieval latency
  cheaply enough to diagnose regressions.
- Do not log hidden reasoning.

## Planner-prompt contract

Update `prompts/planner_system.md` whenever the live contract changes. Teach the
planner clearly without claiming semantic enforcement the runtime does not have.

The prompt must explain:

- world evidence, working outcomes, durable memory, receipts, and fieldbook are
  different;
- `current_observation` means the exact authored planner context, not a later
  observation;
- only IDs present in the current payload/read results may be cited;
- facts and episodes cite already available evidence;
- no-op/unknown/not-executed/advice/belief evidence retains its limits;
- commitments and hypotheses remain intention/uncertainty;
- resolution requires closure evidence;
- exact target IDs come only from fresh current observation;
- old IDs, cell labels, coordinates, keys, and capabilities never authorize
  action;
- continuity operations are optional and concise;
- invalid operations may be rejected independently;
- recent receipts explain what to correct;
- full project content is elective;
- finishing a plan does not automatically finish a commitment or project;
- current telemetry overrides old notes;
- duplicate restatements are not needed to keep a record visible.

Use correct examples:

```json
{"source": "memory", "memory_id": "mem-example"}
```

Never use a numeric memory ID. Keep examples minimal and provider-portable. Do
not turn a detailed Ladle scenario into universal courier behavior.

## Schema, example, config, and metrics hygiene

- Regenerate `decision`, `plan`, `plan_patch`, `observation`, and any new
  continuity/fieldbook schemas from models.
- Add staleness gates for generated artifacts.
- Replace current example `memory_writes` with `continuity_operations` or omit the
  empty field.
- Add one test that loads every checked-in planner JSONL file.
- Remove stale comments describing dead `PlanPatch.memory_writes` behavior after
  preserving historical context in ADR/changelog.
- Rename current metrics toward `continuity_operations_*` and memory lifecycle
  transitions. Keep intentional backwards compatibility when evaluating old logs.
- Keep generic `live.longform.yaml` generic. Either require an explicit campaign
  override/fail closed or add a separately named Ladle profile that owns
  `ladle-css-01`.
- Do not hardcode test-count claims that immediately drift; generate them or state
  the command and dated result.
- Update `STATUS.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, external planner guide,
  campaign guide, and continuity ADR truthfully.

## Documentation discipline

- Amend the existing continuity ADR when tightening the same accepted authority
  decision. Write a new ADR only for a genuinely new durable decision such as the
  planner-context manifest or fieldbook authority.
- ADRs record decisions and consequences, not an engineering diary.
- Guides cover operator procedures: campaign naming, migration, inspection,
  fieldbook use, evaluation, and recovery.
- Reports hold run-specific analysis and evidence.
- Generate tables/catalogs that restate code.
- Label every claim as portable, replay/simulated, Windows integration,
  native-build/load, or supervised live evidence.
- Do not claim live continuity competence until a live run actually exercises it.

## Implementation style

- Build on current Pydantic models, SQLite store, runtime, observation budgeting,
  planner adapters, logging, and eval seams.
- Prefer strict discriminated unions and runtime-owned IDs.
- Prefer explicit columns and typed event payloads. Bounded JSON is acceptable as
  immutable lifecycle payload, not as an excuse to skip schema design.
- Avoid new dependencies when standard-library SQLite and current packages are
  sufficient.
- Preserve real user data through one explicit versioned migration, then remove
  parallel compatibility paths from current authoring.
- Do not copy WorldWeaver code wholesale. Its kept-memory relevance and workshop
  concepts are design evidence; its duplicate side authority and recursive
  prompt feedback are warnings.
- Fix the whole class of a discovered bug. Do not merely move the single-step
  call site while patches remain vulnerable.
- One slice may touch models, store, runtime, planners, prompt, schemas, tests,
  config, metrics, CLI, and docs when that is required for one invariant.
- Do not commit after every test tweak. Make one intentional commit when the
  vertical slice is complete.

## Per-invocation method

1. Establish repository state: branch, `git status`, recent log, current baseline,
   active config, and whether another agent has uncommitted work.
2. Read current `campaign.py`, `continuity.py`, `memory.py`, `models.py`,
   `runtime.py`, planner adapters, observation budgeting, prompt, schemas, tests,
   metrics, configs, and continuity docs.
3. Reproduce each reviewed defect relevant to the first incomplete slice. If one
   is already fixed, cite the exact test and implementation.
4. Classify all six queue slices as absent, partial, or complete.
5. State one compound slice with problem, scope, non-goals, and behavioral
   acceptance criteria. Do not ask for approval when the policy is already here.
6. Write failing behavioral tests and observe the intended failures.
7. Implement the whole vertical path, including migration, schemas, prompt,
   metrics, examples, and docs touched by the invariant.
8. Run focused tests continuously, then full pytest, Ruff, mypy, and generated
   artifact checks.
9. Run targeted mutation tests for the new authority seams.
10. Inspect the diff for live databases, run artifacts, secrets, caches, generated
    drift, unrelated churn, and accidental WorldWeaver copying.
11. Make one intentional commit. Do not push or open a PR unless separately
    authorized.
12. Report what is proven, what is only designed, what could not run, and the
    exact next incomplete dependency.

## Required final report

```markdown
# Memory and Continuity Loop Result

## Slice completed
## Completion matrix
## Defects reproduced before the change
## Why this dependency came next
## Changes
## Authored-context and evidence-authority review
## Data migration and compatibility
## Planner payload and observation-budget review
## Evidence
- portable
- replay/simulated
- Windows/native/live, if any
## Failure-isolation review
## Mutation results
## Prompt/schema/example/config hygiene
## Not tested or not claimed
## Working-tree and commit state
## Next slice and its first failing invariant
```

## Stop conditions

Stop after the selected compound slice is complete, green, reviewed, and
committed. Also stop with a precise report when an actual dependency/platform
failure prevents the next safe local step, a live action needs authorization not
present, or unrelated working-tree changes make the target files unsafe.

Do not stop merely because the design is broad, migration is difficult,
WorldWeaver is messy, or the issue touches several modules. Do not skip to a
prettier later phase. Deliver the strongest complete increment at the first unmet
dependency and leave the next invariant exact.

Begin now by establishing repository state, running the baseline, verifying the
implemented foundation, reproducing the first incomplete reviewed defect, and
selecting the first dependency-ordered slice.
