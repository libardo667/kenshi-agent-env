# Engineering loop — evidence-grounded continuity, the fieldbook, and long-horizon play

Copy this whole document into a capable coding agent whose working directory is the
`kenshi-agent-env` repository root. Reuse it for successive invocations.

This supersedes the earlier memory-and-continuity loop prompt. Slices 1 through 5 have
landed: campaign scope, the planner-context manifest, evidence capability and canonical
provenance, failure isolation and receipts, deterministic recall with elective search,
the private fieldbook, and lossless compaction are real code. Do not restart that work
or replace it with a parallel framework. **Verify the checkout** — later commits may
have advanced past this snapshot, and the slice numbering here was renumbered once
already.

One invocation completes **one compound vertical slice**, leaves the tree green, and
makes one intentional commit. Do not spend an invocation renaming a field, drafting
an ADR, adding unused abstractions, or fixing one example while a related invariant
is broken. Do not mix renderer, movement, combat, or native-protocol work into this
feature.

Work with high agency. Inspect, decide, implement, test, document, commit. Do not
stop for naming approval when this document already sets the policy. Do not turn
ordinary in-game consequences into procedural paralysis; live-input authority remains
in force, but the early slices are portable and need no live input.

**This document is the single source for its own rules.** Invariants are numbered
(`I1`…`I26`) and stated once. Defects (`D1`…`D10`) and slices cite invariant numbers
rather than restating them. If you find yourself needing a rule that is not numbered
here, that is a gap worth reporting, not a license to invent a fifth phrasing of an
existing one.

## Mission

Finish a continuity system that lets a Kenshi-playing agent distinguish:

1. what the game and controller actually proved;
2. what it recently attempted and what happened;
3. what it deliberately chose to remember;
4. which commitments and open questions remain;
5. what larger bodies of work it may deliberately reopen;
6. which exact pieces of context were shown to the planner that authored a
   continuity operation.

The result is not "increase the memory limit." It is a truthful, inspectable boundary
among **world evidence**, **working continuity**, **durable kept memory**, and a
private **fieldbook**.

The feature is complete when an agent such as Ladle can pursue a delivery across many
plans and real process restarts, retain grounded route lessons, maintain a bounded
delivery docket and route atlas, correct or supersede old beliefs, see why a
continuity operation was rejected, and still treat current Kenshi telemetry as
authoritative when its own notes disagree.

This is not a generic cognitive-architecture rewrite. Do not import affective
substrates, simulated needs, souls, pulses, reveries, or WorldWeaver's broad resident
runtime. Strengthen the existing Kenshi planner, executor, observation, logging, and
evaluation architecture.

## Operating posture

- The checkout, `git log`, `STATUS.md`, `CHANGELOG.md`, generated schemas, tests, and
  run evidence are the source of truth. This document is a snapshot.
- Preserve working behavior unless an invariant below requires a migration or
  contract change.
- Fix classes of defects, not one reproduction. If one continuity path can bind to a
  later observation, inspect plan, decision, patch, rebase, advisor, replay, and
  subprocess paths for the same mistake.
- Prefer a complete vertical invariant across models, runtime, store, planner
  adapters, budgeting, schema, tests, metrics, and docs over a locally elegant unused
  class.
- Keep one canonical authority. No side file, cache, Markdown export, or embedding
  index becomes an independent source of memory truth.
- Do not store or request private chain-of-thought. Persist concise explicit facts,
  episodes, commitments, hypotheses, decisions, observations, questions, and project
  notes only.
- Use coding agents aggressively; remain accountable for semantics. A passing test
  that proves the wrong contract is not progress.

## Authority model

Four layers, mandatory, kept distinct.

**1. World evidence.** Telemetry, screenshots, world-state revisions, current exact
references, action receipts, controller-owned semantic evidence, native
acknowledgements, scenario attestations, and runtime-assessed outcomes. The only
evidence that can establish game state or game effects. It answers: is cargo visible
in a complete inventory export; did money increase; did this exact command receive a
causally later terminal acknowledgement; is this exact entity present in fresh
telemetry; did source quantity fall while destination rose by the same amount.

**2. Working continuity.** Bounded, recent, runtime-owned, primarily run-scoped:
action outcomes, plan outcomes, continuity receipts, elective read receipts, and the
planner-context manifest paired with each planner output. It says what was attempted,
what was shown, what changed, and why work ended. It is not durable belief.

**3. Durable kept memory.** Campaign-scoped agent-authored continuity: facts,
episodes, commitments, hypotheses. Every active record has explicit lifecycle and
structured provenance. Secondary to current world evidence.

**4. Private fieldbook.** A larger campaign-scoped workspace for named continuing
bodies of work: delivery dockets, route atlases, incident logs, vendor notes,
equipment plans. Ordinary observations carry only a bounded project index and at most
one selected active project summary. Full entries require an elective bounded read.
The fieldbook is not Kenshi inventory — a note saying "six slop canisters" cannot
create, preserve, transfer, sell, or deliver six in-game items.

## Invariants

These are normative and numbered. Cite them; do not paraphrase them.

### Authority and grounding

- **I1 — Current world evidence wins.** Memory and fieldbook guide inquiry but never
  override fresh telemetry, current exact references, controller receipts, or safety
  state.
- **I2 — Authored context is immutable.** A continuity operation resolves
  `current_observation`, exact target IDs, and every advertised evidence ID against
  the immutable planner context from which that exact output was authored. Commit-time
  state may decide whether the operation still applies; it may never silently
  substitute itself as the source.
- **I3 — Delivered means delivered.** An ID is citable only when the final planner
  input manifest says it was included, or a later elective read placed it in a new
  manifest. Global existence in the run or database is not enough.
- **I4 — Continuity grants no action authority.** A remembered target ID, cell label,
  coordinate, window, key, or capability never authorizes a later game action.
- **I5 — No future success enters memory.** A plan cannot cite or store the success of
  its own future steps. Those runtime-owned IDs do not exist yet.
- **I6 — Stale authored telemetry grounds nothing fresh.** A stale authored
  observation cannot ground a fresh-current-state fact or an exact target.

### Epistemic integrity

- **I7 — Epistemic kinds stay distinct.** Commitment is intention; hypothesis is
  uncertainty; fact is an agent-authored claim grounded in world-capable evidence;
  episode records an observed event or attempt and preserves its failure/unknown
  status.
- **I8 — Evidence capability matters.** References resolve to typed immutable
  snapshots with explicit authority, not directly to strings. Validation uses those
  capabilities before rendering any human-readable summary. Advice, beliefs, no-ops,
  unknowns, and procedural completion retain their limits. See the admissibility
  matrix.
- **I9 — Resolution is earned.** A commitment or hypothesis closes only with explicit
  already-delivered closure evidence. A reason string is not evidence.
- **I10 — Unknown stays unknown.** Incomplete inventory, stale telemetry, missing
  outcome detail, failed reads, and ambiguous references do not become absence, loss,
  success, or certainty.
- **I11 — No opaque forgetting.** Records may leave automatic recall, but deletion,
  semantic rewriting, resolution, supersession, and retraction are explicit.

### Scope and identity

- **I12 — Entity identity is exact and lifetime-bounded.** Names, roles, positions,
  and similarity never reactivate an entity-bound memory.
- **I13 — Campaigns do not bleed.** Unrelated saves, fixtures, characters, and tests
  never share private continuity merely because they use the same config file.
- **I14 — One canonical continuity authority.** No JSONL side store, Markdown export,
  embedding cache, or session log independently injects durable state.

### Budgeting and recall

- **I15 — Recall is not reinforcement.** Reading, prompting, budgeting, and observation
  decoration cannot increase a memory's importance.
- **I16 — Automatic context is bounded.** Exact current-target constraints and open
  commitments may receive protected space. General memories, receipts, and project
  indexes remain bounded.
- **I17 — Retained provenance survives eviction.** Full recent outcomes may be bounded,
  but every issued outcome keeps a compact immutable evidence digest for the run's
  lifetime. Eviction removes display detail, not authority metadata.

### Durability and failure

- **I18 — Structured provenance survives.** Canonical lifecycle history stores the
  planner-authored operation, authored context identity, exact structured references,
  runtime-resolved evidence snapshots, origin, plan/step provenance, and a rendered
  summary. The prose summary is a projection for humans, not the sole durable
  provenance.
- **I19 — Continuity failure is isolated.** A semantically invalid sidecar operation
  receives a typed receipt and does not cancel otherwise valid gameplay. Expected
  semantic and storage conflicts become typed rejected receipts leaving history and
  projection unchanged. Unexpected store failure is distinct, rolled back, explicitly
  degraded, and never masquerades as a normal rejection.
- **I20 — Embeddings are optional retrieval infrastructure.** They never decide whether
  a memory may be stored or whether a claim is true.

### Process

- **I21 — No hidden reasoning persistence.** Do not store private chain-of-thought or
  ask models to reveal it.
- **I22 — Tests prove behavior.** Avoid source-text assertions where an executable
  contract can be tested.
- **I23 — Behavior must be mutation-visible.** Executable authority logic does not live
  inside decorated definitions. See "Mutation testing."
- **I24 — Claims carry evidence labels.** Every claim in docs, reports, and status is
  labeled portable, replay/simulated, Windows integration, native build/load, or
  supervised live. Never collapsed into "supported."
- **I25 — Coverage claims are derived from committed inputs.** A statement that a module
  was mutation-tested, or that N shards remain unattested, is generated and re-checked
  against the current tree — never a number typed into prose. Evidence that lives only on
  the machine that produced it is not a repository claim.
- **I26 — Automated pauses replan; they do not end the stream.** Once a non-human
  safety intervention has cancelled current work and established a causally later,
  fresh, loaded, capability-backed paused observation, the runtime abandons the stale
  plan, tells the planner why it intervened, and requests a new plan from that paused
  revision. Human control that is not explicitly handed back, emergency stop, explicit
  planner stop, exhausted run budget, and failure to establish a safe observable paused
  state remain terminal boundaries.

## Evidence: types and admissibility

Resolve each reference into an immutable structured snapshot before validating an
operation. A useful internal shape:

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

Do not copy whole observations into the memory database. Store the minimum immutable
facts needed to interpret the reference later.

### Authority classes

Use an enum or equally explicit policy. At minimum distinguish:

| class | meaning |
| --- | --- |
| `fresh_world_observation` | exact fresh state at an authored revision |
| `verified_world_effect` | controller-owned terminal or causally supported observed effect |
| `observed_change` | runtime saw a tracked change but may not prove the goal caused it |
| `attempt_changed` | an action executed and something changed |
| `attempt_no_op` | executed, no material tracked effect followed |
| `attempt_not_executed` | executor did not perform it |
| `attempt_unknown` | outcome could not be verified |
| `plan_disposition` | the plan ended a particular way; not a world-effect proof |
| `agent_belief` | an existing memory, with its kind and status |
| `advice` | an advisor brief; never direct world evidence |
| `scenario_attestation` | exact fixture identity; not a claim about an action effect |

Names may differ. **Do not collapse them into one boolean `supported`.**

A generic runtime cannot prove arbitrary natural-language entailment, and an LLM
"truth judge" does not solve this. The runtime classifies evidence by what it is
structurally capable of establishing and rejects invalid combinations. Preserve source
statuses in the planner-visible receipt so misleading prose stays inspectable.

### Admissibility matrix

This matrix is the normative evidence spec for I8 and I9.

| operation | minimum requirement |
| --- | --- |
| keep/supersede **fact** | ≥1 delivered reference capable of describing already-observed world state or effect: fresh exact current observation, controller-verified semantic effect, or a causally later action outcome whose structured status suits the claim class. Advice, memory, hypothesis, commitment, plan outcome, no-op, not-executed, and unknown may supplement but never be sole grounding. |
| keep/supersede **episode** | ≥1 observed-event source: action outcome, plan outcome, or fresh current observation where the episode is already visible. A failed/no-op/unknown attempt may ground an episode *about that attempt*, preserving `no_op`/`not_executed`/`unknown`. Advice or memory alone cannot establish that an episode happened. |
| keep **commitment** | Self-authored after the containing decision/plan/patch passed its acceptance boundary. References optional — it is intention. Must be specific enough to close or abandon later. Reinforce or update one ongoing objective rather than multiplying micro-commitments. |
| keep **hypothesis** | Self-authored, references optional and explanatory only. Remains explicitly uncertain regardless of source quality until a separate operation resolves, supersedes, or retracts it. |
| **reinforce** | Means the agent deliberately chose the record again; never triggered by the observation pump (I15). Supplied references persist as structured snapshots. Advice-only or belief-only reinforcement may raise declared importance but is not new world confirmation. Consider separate `salience` and `confidence` rather than one number implying both. |
| **resolve** | Only active commitments and active hypotheses/questions. `references` non-empty. A world-effect commitment (deliver, purchase, earn, recruit, arrive, transfer, equip, escape) needs ≥1 fresh world state or adequate world-effect reference. Advice, memory, no-op, unknown attempt, or plan completion alone cannot close it. Hypothesis resolution preserves confirmed/rejected/unknown — add a typed disposition if one verb cannot express it. |
| **retract** | Agent-authored with a reason; it withdraws a belief rather than establishing a fact. Never deletes history. |
| **supersede** | Replacement validated under the rules of its new kind. Old record and replacement transition atomically. A conflicting active replacement key produces a rejected receipt and no state change. |

Facts and episodes are corrected by supersession or retraction — never marked
"resolved" as though they were tasks.

## Reviewed defects

Observed in the reviewed snapshot. **Do not trust this list blindly.** Add or run a
focused reproduction against the current checkout first.

Three outcomes are legitimate for each:

1. **reproduced** — fix it in the slice that owns it;
2. **already fixed** — cite the exact code and test proving it, move on;
3. **misdiagnosed** — the defect does not exist and is not merely already fixed. Report
   it as misdiagnosed with evidence and move on. **Do not implement a fix for a defect
   you could not reproduce**, and do not write a test that locks in a non-problem.

| id | defect | violates |
| --- | --- | --- |
| **D1** | `CurrentObservationEvidence` holds only a source name; `render_evidence_reference()` renders whichever `Observation` is passed at commit. Single-step authors from the pre-action observation and applies post-dispatch, so a planner-visible `1/1` can be stored as `current_observation(2/2)`. Same class in rebase, concurrent patch planning, advisor latency, pump advancement, and any future delayed sidecar. | I2, I6 |
| **D2** | Authority checks whether an outcome was ever *issued*, a memory *exists*, an advisor brief was *ever* issued. Sequential IDs are guessable; a stored ID may exist without being in the current prompt. | I3 |
| **D3** | `AgentRuntime._decide()` marks every `observation.memories` record delivered before hosted adapters call `Observation.planner_payload()`. A small budget yields a payload with zero memories while the database records all recalled memories as delivered. | I3 |
| **D4** | `ResolveMemoryOperation.references` defaults to empty and the authority accepts an unsupported reason such as `Delivered.`. Because plan-level continuity is processed before execution, a plan can close a commitment in the same response that merely proposes to satisfy it. | I9 |
| **D5** | Existence is checked, capability is not. A fact or episode can be grounded solely by an advisor brief, another memory, a hypothesis, a commitment, a no-op, an unknown or not-executed outcome, a plan outcome whose objective was never causally established, or an outcome rendered only as `evicted`. | I8 |
| **D6** | `ContinuityLedger` keeps issued IDs after full outcomes leave the window, rendering `action_outcome(ao-1: evicted)`. That proves an ID once existed and loses action kind, assessment, execution status, semantic terminal, target, and revisions. | I17 |
| **D7** | Receipts hold typed references at application time, but the store persists mainly a rendered grounding string. If session logs vanish, canonical history cannot reconstruct which structured references, assessments, statuses, context, and revisions produced the record. | I18 |
| **D8** | Keep A, keep B, supersede A with content whose normalized active key equals B: the unique index raises `sqlite3.IntegrityError`, the transaction rolls back, and the error escapes `ContinuityAuthority` because only `MemoryTransitionError` is handled. | I19 |
| **D9** | Receipts are logged but invisible to the planner. The next planner cannot see that its operation was rejected, accepted as reinforcement, or changed a memory ID, so a deterministic invalid operation repeats. | I19 |
| **D10** | Contract drift: `prompts/planner_system.md` shows a numeric memory ID though the schema requires `mem-...`; checked-in JSONL examples emit removed `memory_writes` and fail strict parsing; comments and metrics keep old terminology; `config/live.longform.yaml` is described as generic but hardcodes `campaign_id: ladle-css-01`; docs carry stale test-count claims; no repository test loads every checked-in planner example against its declared model. | I13, I22, I24 |

Fix D10 items inside the slice owning the contract they belong to. Do not spend an
invocation on cosmetic drift alone.

## Required mechanisms

### Planner-context manifest

One runtime-owned concept representing exactly what a planner call received. Names may
differ; semantics may not.

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

Rules: runtime-owned and immutable; one manifest per planner invocation; records the
exact world revision and exact continuity IDs in the final input; paired with the
planner output through parsing, validation, rebase, execution, and sidecar
application; `current_observation` resolves to `authored_revision`; exact target IDs
validate against the fresh authored observation; evidence IDs must appear in this
manifest or in a typed read result it included; commit-time observation may be
recorded separately for audit but never replaces the authored basis; the manifest is
working history and may be logged without becoming durable memory.

**Honest payload assembly.** Stop marking delivery in `AgentRuntime._decide()` before
adapters budget or serialize. Create one preparation seam yielding both the final
representation consumed by that planner and the manifest of records actually included
— `PreparedPlannerInput`, `PlannerCallContext`, or a planner-returned input receipt;
choose the smallest design working across all planners without duplicated semantics.

Hosted text planners: `Observation.planner_payload()` or its replacement returns
rendered text **and** an inclusion manifest. In-process heuristic/scripted planners may
legitimately treat all attached records as delivered. Subprocess planners: be explicit
about whether the process receives full observation JSON or a budgeted representation.

Record delivery only once the final representation exists, immediately before or as it
is handed over. Define the metric as "included in planner input," not "provider
certainly read every token." A failed provider call may still have an input receipt;
distinguish attempted submission from a parsed response where useful.

### Working continuity digests

Keep full recent `ActionOutcome` and `PlanOutcome` windows for planner context, and a
compact immutable digest for every ID issued in the run (I17).

Action-outcome digest: outcome ID and run ID; plan ID/version and step ID; action kind;
exact target/semantic identity where applicable; executed flag and assessment; command
ID; action-start and completion revisions; controller-owned semantic terminal/status
where present; short bounded evidence summary; timestamp.

Plan-outcome digest: plan outcome ID; original objective; disposition; completed step
IDs or count; actions completed; terminal revision; terminal reason digest; timestamps.

No screenshots, full telemetry, or unbounded prose. A large action budget must stay
reasonable in memory — measure it, do not assume. An indexed run-local SQLite table or
session-log index is fine; a second durable belief authority is not (I14). Automatic
context still shows only the bounded recent window; older digests become citable only
when an explicit bounded read places them in a new manifest.

### Canonical memory provenance

Evolve the schema version transactionally. Per lifecycle transition persist at least:
memory ID and campaign ID; lifecycle event and exact planner-authored operation;
origin (decision, plan, patch, compaction, operator migration); source run ID; plan
ID/version and step ID where applicable; authored context ID and authored world
revision; commit-time world revision where applicable; the exact planner-authored
reference union; runtime-resolved evidence snapshots; rendered grounding;
status/transition result; timestamp; predecessor/successor links.

The `memories` projection may keep a bounded latest grounding summary for recall; the
structured event stays canonical.

Projection rebuild must reproduce lifecycle status; content, kind, salience, target;
reinforcement count and timestamps; resolution reason/disposition; supersession links;
latest delivered timestamp where that remains part of projection; and the structured
source links operator inspection needs.

Migration: preserve v1 and v2 data; back up before destructive schema change or use an
equally strong transactional migration/rollback path; mark old flattened grounding
honestly as legacy/unstructured; do not invent structured references for old rows; make
reopening idempotent; test projection rebuild after migration.

### Store failure isolation

Expected conflicts: preflight active-key conflicts when practical; translate expected
`sqlite3.IntegrityError` constraints into `MemoryTransitionError` or an equally typed
domain rejection; roll back event and projection together; preserve both old and
conflicting active records; emit a receipt with the exact reason; continue otherwise
valid gameplay.

Unexpected I/O, corruption, or database failure: roll back; log a distinct store
failure rather than an ordinary semantic rejection; do not claim memory changed;
disable or quarantine further continuity writes for the run when they cannot be
trusted; keep reads only if their integrity is defensible; report the degraded state to
planner and operator; never silently delete or recreate the live database.

Add a distinct `failed` receipt status if accepted/rejected/no-op cannot describe this
honestly.

### Planner-visible receipts

A bounded runtime-owned receipt ledger. Digest: receipt ID; operation and origin;
accepted/rejected/no-op/failed status; reason; resulting memory ID and status where
any; authored context ID and revision; plan/step provenance; compact evidence summary;
timestamp.

Observation policy: surface a small recent list; preserve the latest rejected/failed
receipt through budgeting; never surface unbounded operation history; clear nothing
merely because it was shown; do not rank durable memory by receipt visibility.

Planner guidance must tell the model to correct the exact rejected operation rather
than repeat it unchanged. A successful receipt may supply a new `memory_id` for a later
reinforce/resolve/supersede.

### Recall and elective search

Deterministic default before any semantic retrieval. Recommended tier order:

1. active ongoing commitments relevant to the campaign;
2. exact memories bound to IDs in the fresh authored observation;
3. unresolved high-priority hypotheses or survival constraints;
4. remaining general active records ranked by declared salience, explicit
   reinforcement, lifecycle, and creation/reinforcement time — **not** delivery time;
5. optional relevance-selected records for remaining slots.

No tier consumes unbounded slots. Exact-target and open-commitment guarantees are
explicit in observation-budget tests.

Elective bounded search/read, added after provenance is correct, must: emit zero
keyboard, mouse, or native primitives; create no world command; spend no pointer,
purchase, or native risk budget; search only the current campaign unless an operator
tool says otherwise; return typed result IDs, source metadata, and honest truncation;
place returned IDs into the next manifest so they become citable; never authorize an
action by itself.

SQLite FTS5, deterministic token matching, or bounded `LIKE` is sufficient first.
Do not add an embedding dependency merely to ship search.

### Private fieldbook

Build only after the D1–D8 provenance and failure-isolation defects are closed. Same
campaign scope, one canonical structured store. Support: create project; append entry;
update bounded summary or status; set or clear one active project; complete, pause, or
abandon; inspect one project or search its entries via elective bounded read.

Project types: delivery docket, route atlas, incident log, vendor ledger, equipment
plan, journal, generic. Statuses: active, paused, completed, abandoned. Entries carry
runtime-owned IDs, timestamps, origin/context provenance, source references where
applicable, and a bounded type (note, decision, observation, incident, manifest, route
entry, expense, question).

**Automatic context** exposes only a bounded index: project ID, title, kind, status,
short summary, entry count, last update, active marker. Do not auto-inject the latest
prose excerpt from every project — that creates a self-feedback loop where writing
makes a topic more visible, causing more writing. At most one explicitly selected
active project gets a bounded summary. Full entries are elective.

**Reads and writes** are cognitive side effects owned by the runtime, not by
`AgentEnvironment` and not as game input. They emit zero controller primitives; create
no Kenshi command; have typed receipts; respect campaign scope; use runtime-owned
project/entry IDs rather than arbitrary paths; enforce hard entry and character limits;
report truncation honestly; follow plan/decision/patch commit timing; fail
independently from gameplay. A Markdown export may exist as a disposable generated
view — deleting or editing it must not change canonical state.

**Physical-world boundary.** A fieldbook manifest is not inventory; current telemetry
remains the source of truth for cargo. Do not add a Python shadow inventory. A future
physical "Courier's Ledger" FCS item may gate access to detailed content only as a
separate experiment with fresh complete inventory evidence and ideally stable
item-instance identity. Not required for the first fieldbook slice.

### Compaction

Wire `prompts/memory_compactor.md` only after lifecycle, structured provenance,
manifest authority, deterministic recall, and fieldbook are complete. Until then keep
it clearly inert and remove stale claims that it is active.

Explicit and bounded: exact source memory IDs selected; all sources in one campaign;
incompatible exact target IDs do not merge; incompatible kinds or epistemic statuses do
not merge; unresolved commitments and hypotheses excluded by default; output preserves
uncertainty and the weakest relevant confidence; the compactor returns a strict
candidate, not a direct mutation; malformed, truncated, refused, or semantically
invalid output changes nothing; applying a candidate atomically creates a replacement
and supersedes the exact sources; source history is never deleted; provider, model,
prompt hash/version, parameters, and source IDs are logged; dry-run and operator
inspection supported.

Compaction must not turn several failed or inconclusive attempts into a durable success
lesson.

### Optional semantic retrieval

Ship deterministic recall and search first. Semantic retrieval is an explicit
switchable treatment: exact-target and open-commitment tiers stay deterministic and
lead; candidate pool and top-k bounded; diversity coefficient and minimum relevance
configured and logged; provider/model, dimensions, thresholds, and fallback in run
metadata; cache keys include memory revision/content hash and provider/model; cached
vectors disposable and rebuildable; unavailable embeddings fall back honestly;
similarity never suppresses storage admission (I20); similarity never proves identity,
contradiction, truth, confidence, or importance; tests use a deterministic fake
embedder; A/B evaluation compares retrieval policy, not prose style.

WorldWeaver's relevance-plus-diversity logic is design evidence, not code to copy. Its
side-store and provider-dependent storage filter are warnings, not targets.

## Dependency-ordered work queue

At the start of every invocation, classify each slice `absent`, `partial`, or
`complete`, citing exact code and tests. Select the **first incomplete dependency**.
Do not skip to the fieldbook because it is more visible. Do not reopen completed
campaign/migration work unless a defect requires it.

**The feature is complete when every slice below is `complete` and every invariant
I1–I26 holds under test.** There is no separate completion list.

### Slice 1 — planner-context authority and honest delivery

Closes **D1, D2, D3**. Establishes **I2, I3, I6**.

One end-to-end manifest and authored-basis path covering rebase, delayed advisor,
patch, single-step, replay, and subprocess. Every planner implementation gets explicit
honest semantics. Final budgeting returns an inclusion manifest and delivery events
match the exact IDs in the final input. No observation-pump write regression. Include
the prompt/example/config fixes from D10 that depend directly on this contract; do not
let cosmetic cleanup replace the slice.

Acceptance tests:

- pre-action `1/1`, post-action `2/2`: the decision's `current_observation` grounding
  stays `1/1`;
- an operation cannot cite an ID issued in the run but absent from its manifest;
- an operation may cite an older record after an explicit read places it in a later
  manifest;
- stale authored telemetry cannot ground a fresh-state fact or exact target;
- a rebased plan never silently rebases its continuity evidence;
- a staged patch's manifest stays paired with that exact patch through application;
- rejected/discarded patch continuity contributes nothing;
- final payloads budgeting 0, 1, N, and all memories create exactly matching delivery
  events;
- delivery semantics tested for OpenAI/OpenRouter preparation without live calls, plus
  subprocess, scripted, heuristic, and replay paths;
- planner failure after input preparation is recorded honestly without inventing a
  parsed output.

### Slice 2 — evidence capability, closure rules, canonical provenance

Closes **D4, D5, D6, D7**. Establishes **I7, I8, I9, I17, I18**.

References resolve to typed snapshots; admissibility follows the matrix; resolve
requires closure evidence and applies only to resolvable kinds; full recent outcomes
plus compact all-run digests exist; evicted references retain assessment and revisions;
canonical events store structured operation/context/evidence provenance; projection
rebuild and migration preserve it; operator inspection shows both structured sources
and rendered grounding.

Acceptance tests:

- advisor-only, memory-only, hypothesis-only, and no-op-only facts rejected;
- unknown/not-executed outcome cannot close a commitment;
- plan completion alone cannot prove a world delivery;
- a failed/no-op outcome may ground an episode that stays explicitly failed/no-op;
- fresh exact observation may ground a fact about that observation;
- controller-verified transfer evidence may close a transfer commitment;
- commitment and hypothesis keeps without world evidence accepted as intention and
  uncertainty;
- resolve with empty references rejected; resolve of fact/episode rejected in favor of
  supersede/retract; resolved hypothesis preserves confirmed/rejected/unknown;
- evidence IDs from another run or campaign rejected;
- exact target memory never attaches by name or stale identity;
- with visible limit 1, `ao-1` retains a digest after `ao-2` evicts its full record, and
  the digest preserves action kind, assessment, execution, semantic status, revisions;
- an explicit read resurfaces the digest under a bounded result;
- eviction never turns a `no_op` into generic "exists" evidence;
- large action budgets stay within an explicit memory/performance bound;
- projection rebuild reproduces exact current state and structured evidence.

### Slice 3 — failure isolation and planner feedback

Closes **D8, D9**, and the remaining **D10**. Establishes **I19**.

Typed rejected receipts for expected conflicts; distinct rolled-back degraded state for
unexpected failure; no invalid continuity operation cancels valid gameplay; receipt IDs
and bounded digests reach the next planner; the latest rejected/failed receipt survives
budgeting; metrics count accepted/rejected/no-op/failed accurately; all checked-in
planner examples parse; old `memory_writes` terminology gone from current outputs with
deliberate reader compatibility retained; generic long-form config no longer hardcodes
Ladle's campaign — add a Ladle profile or an explicit override path; prompt examples use
real string memory IDs; generated schemas, docs, and test-count claims current.

Acceptance tests:

- superseding A with B's active normalized key returns a rejected receipt, leaving A and
  B unchanged;
- event append and projection update roll back together on injected failure;
- an unexpected database failure produces a distinct failed/degraded state;
- closed records refuse invalid transitions;
- cross-campaign IDs unreachable;
- v1/v2 migration idempotent, preserving honest legacy provenance;
- read-only CLI inspection creates no campaign and writes nothing;
- deleting derived caches or Markdown exports changes no canonical result;
- every operation receives one receipt ID; accepted receipts expose the resulting memory
  ID; the latest rejected/failed receipt survives a tight budget; the next planner can
  correct the exact rejected operation; receipt visibility does not reinforce durable
  memory; receipt collections stay bounded;
- every checked-in JSONL planner example parses against the current strict model;
  generated schemas fresh; prompt examples pass a contract test or fixture parse;
  configs cannot silently share a named real campaign undocumented; old log/eval
  compatibility deliberate and tested.

### Slice 4 — deterministic recall, elective search, fieldbook

Establishes **I15, I16**, and the fieldbook layer of the authority model.

May be one compound slice or two coherent invocations — one for read/search, one for
fieldbook — if the tree makes that boundary real. Do not split into model-only,
table-only, and prompt-only micro-slices.

Protected open-commitment and exact-target tiers; deterministic bounded search/read with
typed receipts; returned read IDs citable only in the next manifest; campaign-scoped
projects and append-only entries; bounded index and one active project; elective reads;
zero game input and zero game-risk spend; fieldbook text cannot create or override
inventory; restart persistence and campaign isolation; migration, schema, CLI, metrics,
prompt, and docs complete.

Acceptance tests:

- project creation, append, status, active selection, read, pause, complete, abandon
  round-trip;
- project and entry IDs runtime-owned;
- campaign isolation and restart persistence;
- automatic context contains index metadata but not every full entry;
- elective read bounded, reporting truncation;
- fieldbook operations emit zero controller primitives and no world command;
- arbitrary paths cannot escape or bypass the structured store;
- Markdown export disposable;
- a fieldbook manifest saying six items cannot change telemetry inventory;
- incomplete inventory stays unknown rather than lost.

### Slice 5 — compaction and optional semantic retrieval

Establishes **I20**.

Provenance-preserving candidate compaction with atomic application; deterministic recall
remains default; semantic MMR retrieval optional, explicit, logged, disposable; storage
admission stays deterministic; provider outage falls back without changing canonical
memory; controlled tests and A/B metrics exist.

### Slice 6 — Ladle restart evaluation

**In flight, uncommitted, and red.** `src/kenshi_agent/evals/restart_continuity.py` and
`tests/test_restart_continuity_eval.py` exist untracked and most of their contract passes.
Finish them; do not start over. Three tests fail, and two of the causes are the same
mistake:

- `test_complete_evidence_contract_is_observable` and
  `test_real_process_bundle_matches_the_complete_contract` assert pinned SHA-256 digests
  of the whole bundle. The implementation moved after the digests were pinned. **A pinned
  whole-document digest is a freshness check wearing a test costume** — it fails on every
  change without saying which field changed or whether the change was wrong. Replace them
  with assertions on the contract's actual shape, or generate the expected bundle.
- `test_process_bundle_contains_only_the_declared_evidence_tree` fails because
  `artifact_files` omits `evidence.json`; decide whether the bundle indexes itself and
  make both sides agree.

Its shard reports 158 open mutants, concentrated in `_phase_two`, `_working_outcomes`,
`_phase_one`, and `_metrics`. Attend them before calling the slice done.

A reproducible evaluation around a cargo-delivery campaign, using synthetic, mock,
replay, fixture-attested, or live evidence at the strongest level the repository can
honestly support.

Must include: a campaign-scoped open commitment to deliver a fixed cargo quantity;
multiple plans and action outcomes including at least one no-op, failed, or inconclusive
attempt; route or incident details written to the fieldbook rather than compressed into
memory; a real process restart on the same campaign ID; the second process receiving the
unresolved commitment and bounded project index; an elective read of relevant material;
current telemetry disagreeing with an old note, telemetry winning; a same-named
different entity receiving no old exact-entity memory; a different campaign receiving
none of Ladle's continuity; commitment resolution only after cited closure-capable
evidence; exact manifests in the evidence bundle; rejection feedback producing a
corrected next operation rather than unchanged repetition.

A supervised integration run may expose a cross-cutting runtime defect while exercising
this slice. Such a defect is part of the slice when it violates an invariant here. In
particular, prove that a verified automated safety pause returns to strategic planning
under I26 without resuming the cancelled plan; keep human-held control, emergency stop,
and unobservable cleanup failure terminal.

Compare at least: continuity disabled or pre-feature baseline; scoped lifecycle memory;
memory plus fieldbook; deterministic versus semantic retrieval if it exists.

Measure: repeated no-ops; resumed commitments; stale-memory corrections; unsupported
success claims; cross-campaign leaks; evidence-reference rejection rate; correction
after rejection; fieldbook reads and prompt cost; exact delivered-memory counts; restart
continuity; eventual delivery status.

Do not use personality resemblance or preferred prose as a success metric. Do not claim
general Kenshi competence from one successful delivery.

After portable and replay evidence is green, run the strongest safe supported
integration proof — a useful endpoint is the same explicit campaign across two supported
`./dev play`/`./dev journey` pair, the second planner demonstrably receiving and using a
grounded unresolved commitment or route lesson. Live input acknowledgements and human
supervision rules remain authoritative. Use only the checked-in `./dev` workflow for live
control. Treat a launcher, recovery, display-lease, or journey-orchestration defect as
goal work; never replace missing infrastructure with ad-hoc input commands that obscure
what the supported planner path can actually do.

## Mutation testing

Mutation testing applies to the authority seams this feature creates, not as a
project-wide ritual that delays it. **Attend a module's shard in the same slice that
changes it.** Do not postpone a slice until every unrelated shard is attended.

### Attendance is derived, not remembered (I25)

An earlier invocation added 275 lines to `runtime.py` and 100 to `continuous_executor.py`
and attended neither shard. Nothing caught it, because the rule above was prose and the
run artifacts were machine-local and silent about which tree they attested. Both are now
fixed, so **do not decide from memory which shards you touched** — derive it:

```bash
git diff --name-only <slice-base> -- src/kenshi_agent   # what this slice changed
grep '^| `' docs/generated/MUTATION_ATTESTATION.md      # what is currently attested
```

`docs/generated/MUTATION_ATTESTATION.md` is committed and regenerated by
`python scripts/export_mutation_ledger.py`, which folds in `runs/mutation/` and then
re-derives each shard's state by digesting the module again. A module you edited reads
`source-changed` until you re-run its shard, and `tests/test_docs_hygiene.py` fails until
the ledger is regenerated — so a stale claim breaks the build rather than sitting in the
file. Regenerate and commit the ledger in the same slice.

The four states mean different things and only one of them is done:

- `attested` — a campaign ran and the module is byte-identical since.
- `source-changed` — you edited it afterwards. **This is the state that must not survive
  your commit** for any module in your slice's diff.
- `unverified` — the campaign predates source digests. It is not a pass; re-run the shard
  to replace it.
- `never` — no campaign has ever been recorded. Not a pass either.

`--allow-actionable` records a *classification* baseline, not a finished shard. A shard
with open mutants is attended, not clean; the ledger's `open` column is the honest count
and `cli` currently carries 1076 of them. Do not report a shard as done while that column
is non-zero unless every survivor is documented as equivalent.

Run campaigns on a frozen tree. `_assert_batch_inputs_unchanged` refuses a run whose
inputs moved underneath it, so finish edits — including documentation — before starting.

### Mutation visibility is a precondition (I23)

`mutmut` skips **any decorated class** and **any decorated function or method** except a
single bare `@staticmethod` or `@classmethod`. That includes `@property`,
`@cached_property`, and every pydantic `@field_validator`/`@model_validator` pair, since
those are two decorators.

Consequences you must respect:

- **A shard generating zero mutants is a failure, not a pass.** It is indistinguishable
  from perfection and means nothing was examined. `ExecutionToken` already produced this
  exact false green; `mutation_campaign.py` now fails closed on `summary.total == 0`.
- Before a new authority seam counts as attended, its executable behavior must live
  outside decorated definitions. Keep decorated classes **data-only**; put decisions in
  undecorated classes or module-level functions. `_ExecutionTokenState` versus
  `ExecutionToken` is the pattern to copy.
- This applies to everything this feature introduces: `PlannerContextManifest`,
  `ResolvedEvidenceSnapshot`, `ContinuityReceipt`, `FieldbookProject`, read receipts,
  digests. All of them will be reached for as decorated models by default.
- `PlanBudgetLedger` in `planning.py` is currently a decorated class with real decision
  logic and has never been mutated, inside a module with a published mutation report.
  Fix it in the first slice that touches planning.
- Add a portable test asserting that no decorated definition under `src/kenshi_agent/`
  contains an `If`, `Compare`, `BoolOp`, `BinOp`, or `IfExp` in its body, with an
  allowlist requiring a stated reason in the style of `ROOT_DOC_EXEMPTIONS`.

Verify shard names with `./mutate list` before running. A misspelled batch fails loudly;
a silently unattended module does not.

### Mutants that must die

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
- partially append without projection update, or the reverse;
- let a unique-key conflict escape as a raw SQLite error;
- hide the latest rejected receipt under budgeting;
- leak another campaign's record or fieldbook project;
- reactivate by display name;
- surface every fieldbook excerpt automatically;
- treat fieldbook prose as inventory;
- delete compaction sources;
- let embedding availability change storage admission.

Kill them or document a genuinely equivalent, non-actionable mutation. Diagnostic-only
exclusions cover exception and evidence *prose* where wording cannot alter authority or
state — never a predicate, authority value, revision, evaluation, identifier, or
decision.

## Performance and observation-budget rules

- The pump may run ~10 Hz. Continuity decoration must not perform write transactions at
  that rate.
- Build the manifest once per planner call, not per pump tick.
- Index campaign, lifecycle status, kind, target, project, entry order, and
  deterministic search.
- Bound all automatic collections and elective read results.
- Preserve the latest action/plan outcome, latest rejected/failed receipt, open
  commitments, and exact current-target memories before optional general context.
- A deliberately requested read either fits its documented bound or returns typed
  truncation/unavailability. Never silently drop the chosen source.
- Outcome digests must be compact enough for the maximum supported run budget — measure
  the cost, do not assume it.
- Log candidate counts, included IDs, payload characters, and retrieval latency cheaply
  enough to diagnose regressions. Do not log hidden reasoning (I21).

## Planner-prompt contract

Update `prompts/planner_system.md` whenever the live contract changes. Teach the planner
clearly without claiming semantic enforcement the runtime does not have.

The prompt must explain: world evidence, working outcomes, durable memory, receipts, and
fieldbook are different; `current_observation` means the exact authored context, not a
later observation; only IDs present in the current payload or read results may be cited;
facts and episodes cite already-available evidence; no-op/unknown/not-executed/advice/
belief evidence retains its limits; commitments and hypotheses remain intention and
uncertainty; resolution requires closure evidence; exact target IDs come only from fresh
current observation; old IDs, cell labels, coordinates, keys, and capabilities never
authorize action; continuity operations are optional and concise; invalid operations may
be rejected independently; recent receipts explain what to correct; full project content
is elective; finishing a plan does not finish a commitment or project; current telemetry
overrides old notes; duplicate restatements are not needed to keep a record visible.

Use correct examples:

```json
{"source": "memory", "memory_id": "mem-example"}
```

Never a numeric memory ID. Keep examples minimal and provider-portable. Do not turn a
detailed Ladle scenario into universal courier behavior.

## Repository hygiene

- Regenerate `decision`, `plan`, `plan_patch`, `observation`, and any new
  continuity/fieldbook schemas from models, with staleness gates.
- Replace example `memory_writes` with `continuity_operations`, or omit the empty field.
- One test loads every checked-in planner JSONL file.
- Remove stale comments describing dead `PlanPatch.memory_writes` behavior after
  preserving the historical context in ADR or changelog.
- Rename metrics toward `continuity_operations_*` and memory lifecycle transitions,
  keeping intentional backwards compatibility for old logs.
- Keep `live.longform.yaml` generic: require an explicit campaign override that fails
  closed, or add a separately named Ladle profile owning `ladle-css-01`.
- Never hardcode test-count or coverage claims that drift; generate them or state the
  command and dated result. `STATUS.md` carried "Fifty-six mutation shards remain
  unattested" while the real number was fifty; it now links the generated ledger instead
  (I25). A count typed into prose is a claim nothing re-checks.
- Update `STATUS.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, the external planner guide, the
  campaign guide, and the continuity ADR truthfully.

## Documentation discipline

- Amend the existing continuity ADR when tightening the same accepted decision. Write a
  new ADR only for a genuinely new durable decision such as manifest or fieldbook
  authority.
- ADRs record decisions and consequences, not an engineering diary.
- Guides cover operator procedures: campaign naming, migration, inspection, fieldbook
  use, evaluation, recovery.
- Reports hold run-specific analysis and evidence, dated and write-once.
- Generate any table or catalog that restates code.
- Label every claim per I24. Do not claim live continuity competence until a live run
  exercises it.

## Implementation style

- Build on current Pydantic models, SQLite store, runtime, observation budgeting,
  planner adapters, logging, and eval seams.
- Prefer strict discriminated unions and runtime-owned IDs.
- Prefer explicit columns and typed event payloads. Bounded JSON is acceptable as an
  immutable lifecycle payload, not as an excuse to skip schema design.
- Avoid new dependencies where stdlib SQLite and current packages suffice.
- Preserve real user data through one explicit versioned migration, then remove parallel
  compatibility paths from current authoring.
- Do not copy WorldWeaver code wholesale. Its kept-memory relevance and workshop concepts
  are design evidence; its duplicate side authority and recursive prompt feedback are
  warnings.
- Fix the whole class of a discovered bug. Do not merely move the single-step call site
  while patches remain vulnerable.
- One slice may touch models, store, runtime, planners, prompt, schemas, tests, config,
  metrics, CLI, and docs when one invariant requires it.
- Do not commit after every test tweak. One intentional commit per completed slice.

## Per-invocation method

1. Establish repository state: branch, `git status`, recent log, baseline, active config,
   and whether another agent has uncommitted work. **Another agent may be working in this
   tree right now.** If `git status` shows changes you did not make, leave them alone,
   stage your own paths by name rather than `git add -A`, and re-check `git status`
   immediately before committing.
2. Read current `campaign.py`, `continuity.py`, `memory.py`, `models.py`, `runtime.py`,
   planner adapters, observation budgeting, prompt, schemas, tests, metrics, configs, and
   continuity docs.
3. Run the baseline before editing:
   ```bash
   uv run pytest -q
   uv run ruff check .
   uv run mypy src
   ```
   If dependency infrastructure is unavailable, record the exact failure and run the
   strongest available focused suite. A package mirror outage is not a green baseline.
4. Reproduce each defect relevant to the first incomplete slice, classifying it
   reproduced, already fixed, or misdiagnosed.
5. Classify all six slices absent, partial, or complete.
6. State one compound slice: problem, scope, non-goals, behavioral acceptance criteria.
   Do not ask for approval when the policy is already here.
7. Write failing behavioral tests and observe the intended failures.
8. Implement the whole vertical path, including migration, schemas, prompt, metrics,
   examples, and docs the invariant touches.
9. Run focused tests continuously, then full pytest, Ruff, mypy, and generated-artifact
   checks.
10. Freeze the tree, then run a mutation shard for **every module in
    `git diff --name-only <slice-base> -- src/kenshi_agent`** — derived from the diff, not
    recalled. Regenerate and commit `docs/generated/MUTATION_ATTESTATION.md`; no module in
    your diff may still read `source-changed`, `unverified`, or `never` (I25).
11. Inspect the diff for live databases, run artifacts, secrets, caches, generated drift,
    unrelated churn, and accidental WorldWeaver copying.
12. Make one intentional commit. Do not push or open a PR unless separately authorized.
13. Report what is proven, what is only designed, what could not run, and the exact next
    incomplete dependency.

## Required final report

```markdown
# Memory and Continuity Loop Result

## Slice completed
## Completion matrix (slices × absent/partial/complete, with citations)
## Invariants newly established or strengthened (by number)
## Defects: reproduced / already fixed / misdiagnosed, with evidence
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
## Mutation results (the ledger rows for every module in this slice's diff, plus any shard still not `attested` and why)
## Where I could have gone the other way
## Prompt/schema/example/config hygiene
## Not tested or not claimed
## Working-tree and commit state
## Next slice and its first failing invariant
```

## Stop conditions

Stop after the selected compound slice is complete, green, reviewed, and committed. Also
stop with a precise report when a real dependency or platform failure prevents the next
safe local step, a live action needs authorization not present, or unrelated
working-tree changes make the target files unsafe.

Do not stop merely because the design is broad, migration is difficult, WorldWeaver is
messy, or the issue touches several modules. Do not skip to a prettier later phase.
Deliver the strongest complete increment at the first unmet dependency and leave the
next invariant exact.

Begin now by establishing repository state, running the baseline, verifying the
implemented foundation, reproducing the first incomplete defect, and selecting the first
dependency-ordered slice.
