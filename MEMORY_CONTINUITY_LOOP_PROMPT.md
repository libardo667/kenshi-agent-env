# Engineering loop — evidence-grounded memory, continuity, and fieldbook

Copy this entire document into a capable coding agent whose working directory is
the `kenshi-agent-env` repository root. Reuse the same prompt for successive
invocations. Treat the current checkout, `git log`, `STATUS.md`, generated
schemas, and tests as the source of truth. The description below names the
starting defects visible in the July 27 snapshot; verify every one before acting.

One invocation completes **one compound vertical slice**, leaves the tree green,
and makes a single intentional commit. Do not fragment one invariant across a
series of micro-commits or spend an invocation only renaming classes, drafting an
ADR, or adding unused scaffolding.

## Mission

Build a robust continuity system that lets a Kenshi-playing agent distinguish:

1. what the game and controller actually proved;
2. what it recently tried and why;
3. what it has deliberately chosen to remember;
4. what commitment it is currently carrying;
5. what larger body of work it may deliberately reopen later.

The result is not “put more history in the prompt.” It is a truthful boundary
among **world evidence**, **working continuity**, **durable kept memory**, and a
private **fieldbook**.

The feature is complete when an agent such as Ladle can pursue a delivery over
many plans and process restarts, remember grounded route lessons, maintain a
bounded delivery docket and route atlas, correct or supersede old beliefs, and
still treat current Kenshi telemetry as authoritative when its notes disagree.

This is not a generic cognitive-architecture rewrite. Do not import affective
substrates, simulated needs, souls, pulses, reveries, or WorldWeaver's broad
resident runtime. Strengthen the existing Kenshi planner/executor architecture.

## Current starting point to verify

Inspect the checkout rather than trusting this summary. In the reviewed snapshot:

- `recent_action_outcomes` is a useful bounded per-journey working ledger, with
  the long-form profile retaining sixteen entries, but `ActionOutcome` has no
  stable outcome ID or explicit plan/step provenance.
- `MemoryStore` is one SQLite table keyed by namespace, kind, content, and exact
  optional target ID. General recall is salience/access ordered; exact current
  entity memories receive a separate budget.
- `MemoryStore.recall()` updates `last_accessed_at`, and `_with_memories()` is also
  used by the high-frequency observation pump. Merely decorating observations can
  therefore mutate the database and repeatedly refresh recalled rows before a
  planner actually sees them.
- Continuous `_remember_plan()` stores a plan's `memory_writes` immediately after
  plan validation and before execution. It also creates an automatic “Set out
  to…” episode when no commitment was supplied.
- Single-step memory writes are committed after dispatch, but the free-text write
  can still claim an outcome the receipt did not prove.
- `PlanPatch.memory_writes` exists in the schema but is not clearly committed at
  the exact point where a staged patch is accepted and applied. No model-authored
  continuity field may remain decorative or dead.
- Durable scope is a static configured `run_namespace`; it is not a stable
  campaign/save-lineage identity. This can mix unrelated characters or saves.
- `prompts/memory_compactor.md` exists, but compaction is not an end-to-end
  lifecycle with provenance, atomic supersession, and a recorded model policy.
- Exact entity-scoped recall, stale-telemetry exclusion, observation-budget
  preservation of current-target memories, generated schemas, and strict hosted
  planner contracts are valuable existing work. Preserve them.

## Selective WorldWeaver reference

When a sibling checkout is available, inspect these as design evidence only:

- `worldweaver/ww_agent/src/runtime/memory.py`
- `worldweaver/ww_agent/src/runtime/workshop.py`
- `worldweaver/ww_agent/src/runtime/reference_core.py`
- `worldweaver/ww_agent/src/runtime/process_state.py`
- `worldweaver/ww_agent/src/runtime/ledger.py`
- `worldweaver/research/audits/cognitive-core/memory-identity-and-authority.md`
- `worldweaver/research/audits/cognitive-core/model-authorship-self-feedback-and-dead-schema.md`
- `worldweaver/prune/majors/135-let-residents-make-real-hearth-belongings-and-carry-them-between-worlds.md`

WorldWeaver is a quarry, not a template. Keep these lessons:

- deliberate keeps differ from automatically retained history;
- re-keeping may reinforce without duplicating;
- relevance plus diversity can improve bounded recall;
- private work needs a structurally bounded home;
- one explicit open activity can survive a restart;
- a project index is better than injecting every private excerpt into every
  prompt;
- written descriptions do not create physical world objects;
- a side file that can independently inject memory is a second authority;
- embedding configuration is cognitive policy and must be explicit and logged.

Do **not** copy the legacy side-store authority, automatic workshop summaries,
mixed-authority pulse schema, broad cognitive core, or code wholesale. Reimplement
needed ideas under this repository's own design and license.

## Authority model

The following four layers are mandatory. Exact class and table names may differ,
but their authority must not blur.

### 1. World evidence

Telemetry, screenshots, world-state revisions, exact current references, action
receipts, controller-owned semantic evidence, action outcomes, and scenario
attestations are the only evidence that can establish game state or game effects.

World evidence answers questions such as:

- Is the cargo currently in inventory?
- Did money increase?
- Did this exact command receive a causally later terminal?
- Is this the same currently observed entity?
- Did the selected character move or become unconscious?

### 2. Working continuity

This is bounded, recent, and primarily run-scoped. It includes action outcomes
and plan outcomes: what was attempted, why, whether it changed anything, which
plan and step owned it, and why a plan completed, failed, or was abandoned.

Working continuity is not durable belief. It prevents local repetition and gives
the next plan a truthful account of recent work.

### 3. Durable kept memory

This is campaign-scoped, agent-selected continuity: facts, episodes,
commitments, and hypotheses that may affect later decisions. Every memory is an
agent-authored record with explicit grounding and lifecycle. It is never raw
world authority.

### 4. Private fieldbook

This is a larger campaign-scoped workspace for named, continuing bodies of work:
delivery dockets, route atlases, incident logs, vendor notes, equipment plans,
or other projects. Ordinary observations contain only a bounded project index
and, at most, one explicitly selected active-project summary. Full entries are
available only through an elective bounded read.

The fieldbook is not Kenshi inventory. A note saying “six slop canisters” cannot
create, preserve, transfer, sell, or deliver six in-game items.

## Non-negotiable invariants

1. **Current world evidence wins.** Memory and fieldbook text may guide inquiry;
   they never override fresh telemetry, exact current references, controller
   receipts, or safety state.
2. **Continuity grants no action authority.** A remembered target ID, cell label,
   coordinate, window, or capability cannot authorize a later action. Every game
   action still binds and revalidates against the current observation and input
   lease.
3. **No future success enters memory.** A plan cannot store “I delivered the
   cargo,” “the purchase succeeded,” or an equivalent episode before already
   visible evidence exists. The current plan's future steps cannot be cited as
   evidence.
4. **Epistemic kinds remain distinct.** A commitment is an intention; a
   hypothesis is uncertain; a fact is agent-authored text grounded in observed
   evidence; an episode records an event or attempt and preserves inconclusive or
   failed status where applicable.
5. **Source references are real and runtime-owned.** Planner-authored continuity
   may cite only exact evidence IDs advertised in the current observation. It may
   never invent outcome, event, memory, project, entity, or revision IDs.
6. **Entity memories remain exact and lifetime-bounded.** Names, roles, positions,
   and similarity never reactivate a target-bound memory. Stale telemetry offers
   no current target IDs.
7. **Campaigns do not bleed.** Unrelated saves, fixtures, characters, and test
   runs never share durable memory or fieldbook projects merely because they use
   the same config profile or display name.
8. **Recall is not reinforcement.** Reading or decorating an observation may
   record diagnostics, but it cannot increase priority or importance. Only an
   explicit accepted reinforce operation does that.
9. **Automatic context is bounded.** Open commitments and exact current-target
   constraints may receive protected space. General memories and project indexes
   remain bounded. Full fieldbook entries are elective.
10. **One canonical continuity authority.** Do not create a JSONL sidecar or
    Markdown file that can independently inject memory or project state. Derived
    indexes and human-readable exports are disposable and rebuildable.
11. **Unknown stays unknown.** Missing provenance, a lost receipt, an unavailable
    fieldbook read, or a malformed compaction cannot become success, absence, or a
    confident fact.
12. **No hidden chain-of-thought persistence.** Store explicit concise records,
    decisions, observations, questions, and project notes. Never request or save
    private reasoning traces.
13. **No opaque forgetting.** Automatic recall may omit old records, but durable
    records are not silently deleted or semantically rewritten. Resolution,
    supersession, retraction, abandonment, and compaction are explicit events.
14. **Embeddings are optional retrieval infrastructure.** They never decide
    whether a memory may exist. Their provider, model, thresholds, candidate
    budget, and diversity policy are recorded per run; their cache is disposable.
15. **A rejected continuity update cannot corrupt gameplay.** Structurally invalid
    planner output still fails normal schema validation. A semantically invalid
    sidecar operation is rejected with a typed receipt and feedback, while an
    otherwise valid game plan remains eligible to execute unless that operation
    was itself required to define the plan's scope.

## Required target contracts

Choose concise names consistent with the repository, but implement these
semantics end to end.

### Runtime-owned evidence identities

Every action outcome must have a stable runtime-owned ID and enough provenance
to identify its source:

- run ID;
- plan ID and version when applicable;
- step ID or single-step identity;
- command ID when one exists;
- action-start and completed world revisions when available;
- typed assessment and semantic receipt status;
- timestamps.

Add a bounded plan-outcome ledger with runtime-owned IDs, original plan objective,
completion/failure/abandonment reason, completed step IDs, and terminal revision.
Do not make the next planner reconstruct a plan's purpose from “Execute step X.”

### Evidence references

Continuity operations use a strict discriminated union of bounded references such
as:

- current observation/world revision;
- action outcome ID;
- plan outcome ID;
- existing memory ID;
- advisor brief ID, clearly marked as advice rather than world evidence;
- scenario attestation ID or equivalent stable fixture evidence where useful.

A reference is validated against the exact current observation or continuity
store before an operation commits. Store its source run and minimum immutable
metadata needed for later audit, but do not copy current world state into a rival
authority.

### Memory lifecycle

Replace or evolve plain `memory_writes` into strict typed operations. At minimum:

- `keep` — create a new record;
- `reinforce` — explicitly refresh an existing record without duplicating it;
- `resolve` — close an open commitment or question with evidence/reason;
- `supersede` — create a replacement and link the old record to it atomically;
- `retract` or `release` — remove a record from active recall without deleting
  history.

A memory record needs at least:

- runtime-owned memory ID and campaign scope;
- kind: fact, episode, commitment, or hypothesis;
- active lifecycle status;
- content and optional exact target ID;
- grounding/authorship classification;
- source references;
- confidence where uncertainty is meaningful;
- created, reinforced, resolved, superseded, and recalled timestamps as separate
  concepts;
- reinforcement count;
- links to predecessor/successor records.

Exact duplicate reinforcement may use a deterministic normalized key. Do not add
provider-dependent semantic deduplication to the storage boundary.

### Canonical store

Evolve `MemoryStore` into one versioned continuity store, or build an equally
clear single-authority replacement. Prefer one SQLite database with:

- an append-only lifecycle event/history table;
- a rebuildable current memory projection;
- campaign-scope metadata;
- fieldbook projects and append-only entries;
- schema versioning and transactional migration.

All event append and projection updates occur in one transaction. A failed write,
invalid transition, process interruption, or projection rebuild must neither
partially apply nor lose the old state. SQLite foreign keys remain enabled.

Do not run tests against the user's live memory database. Provide a tested,
idempotent migration with an exact backup or explicit preflight path. Legacy rows
must be preserved with honest `legacy_unverified` provenance and must not be
silently assigned to an unrelated new campaign.

### Campaign identity

Add an explicit stable `campaign_id` or equivalent. A config profile name is not
campaign identity. A character display name is not campaign identity. An exact
fixture `save_id` may seed a controlled experiment, but long-running save lineage
needs an explicit campaign identity that survives ordinary save progress.

Expected policy:

- durable continuity enabled in live mode requires an explicit campaign ID, or
  an explicit ephemeral/run-scoped mode;
- attested scenario tests may use a deterministic scenario campaign;
- mock and replay tests provide their own scope;
- a missing scope fails closed or becomes explicitly ephemeral, never global
  `default` memory.

### Planner continuity output

Planner decisions, plans, and patches need one coherent continuity sidecar.
Avoid three subtly different implementations.

Commit timing is exact:

- a plan's continuity operations are processed only after the plan passes schema,
  current-basis, graph, condition, control-mode, and budget validation;
- a staged concurrent patch contributes no continuity until that exact patch is
  revalidated and actually applied;
- a rejected, stale, foreign, or discarded patch contributes nothing;
- facts and episodes may cite only evidence already visible before the operation;
- commitments and hypotheses may be self-authored but remain typed as intention
  or uncertainty;
- plan/objective audit events are runtime-owned working history, not synthetic
  durable episodes.

Every operation produces an accepted/rejected/no-op receipt. Surface recent
receipts to the planner so a deterministic invalid update is not repeated.

### Recall

Implement a deterministic default recall policy before semantic retrieval.
Recommended tier order:

1. active commitments relevant to the current campaign;
2. memories bound to exact current target IDs;
3. unresolved high-priority hypotheses or survival constraints;
4. remaining general records ranked by declared salience, explicit
   reinforcement, lifecycle, and recency of creation/reinforcement — not recency
   of automatic recall;
5. optional relevance-selected records for remaining slots.

Recall must be a read-only operation in the observation pump. If “delivered to a
planner” diagnostics are useful, mark delivery only when a planner payload is
actually assembled, and do not use that timestamp for importance ranking.

Add an elective bounded memory search/read path for material outside automatic
recall. It emits zero game input, cannot authorize an action, and returns a typed
bounded result to the next planner call. Literal/FTS search is a valid first
implementation; do not add an embedding dependency merely to ship search.

### Fieldbook

Use a structured campaign-scoped store rather than arbitrary planner-selected
filesystem paths. At minimum support:

- create project;
- append entry;
- update bounded summary or status;
- set or clear one active project;
- complete, pause, or abandon project;
- inspect one project or query its entries through an elective bounded read.

Project status should be explicit, such as active, paused, completed, or
abandoned. Entries should carry runtime-owned IDs, timestamps, source references,
and a bounded type such as note, decision, observation, incident, manifest, or
route entry.

Ordinary observations expose only a bounded index: project ID, title, kind,
status, short summary, last update, and perhaps one active project. Do not inject
latest excerpts from every project. A requested read receives a hard character
and entry limit with honest truncation metadata.

Fieldbook writes are local cognitive side effects, not game actions. They emit no
keyboard, mouse, or native primitives and spend no pointer, purchase, or native
risk budget. Invalid fieldbook operations fail independently and visibly.

A human-readable Markdown export may be added as a generated view, but SQLite
remains authoritative and deleting the export changes no behavior.

### Compaction

Wire `prompts/memory_compactor.md` only after lifecycle, provenance, scope, and
projection rebuilding are complete. Otherwise remove or clearly mark it inert.

Compaction must be explicit and bounded:

- exact source memory IDs are selected;
- no cross-campaign compaction;
- no merging different exact target IDs;
- no merging incompatible kinds or epistemic statuses;
- unresolved commitments and unresolved hypotheses are excluded by default;
- the output preserves uncertainty and the weakest relevant confidence;
- the compactor returns a strict candidate, not a direct mutation;
- malformed, truncated, refused, or semantically invalid output changes nothing;
- applying a candidate atomically creates the replacement and supersedes the
  exact sources;
- provider, model, prompt hash/version, parameters, and source IDs are logged;
- dry-run and inspection are supported.

Compaction must never delete the source history.

### Optional semantic retrieval

Ship deterministic recall first. Then add semantic MMR retrieval only as a
switchable treatment:

- exact-target and active-commitment tiers remain deterministic and lead;
- candidate pool and top-k are bounded;
- diversity coefficient and minimum relevance are configured and logged;
- cache keys include memory revision/content hash plus provider/model;
- cached vectors are a disposable index;
- a missing provider falls back honestly to deterministic recall;
- semantic similarity does not suppress storage or prove contradiction, truth,
  identity, or importance;
- tests use a deterministic fake embedder;
- run metadata makes retrieval-policy changes visible in evaluation.

## Dependency-ordered work queue

At the start of every invocation, classify each slice as absent, partial, or
complete from code and tests. Work on the first incomplete dependency. Do not
skip ahead to embeddings or a pretty fieldbook UI.

### Slice 1 — repair authority and timing

Deliver one coherent vertical change that:

- gives action outcomes stable IDs and explicit plan/step provenance;
- adds bounded plan outcomes carrying original objectives and terminal reasons;
- makes observation-pump recall read-only;
- removes recall-time priority reinforcement;
- establishes strict evidence-reference models;
- prevents continuous plans from committing facts/episodes about future work;
- removes the automatic “Set out to…” durable episode or replaces it with honest
  working plan history;
- gives accepted plans, single-step decisions, and actually applied patches one
  consistent continuity-operation path;
- proves rejected/stale/discarded patches write nothing;
- eliminates any dead continuity schema field.

Do not stop after adding IDs or models. Wire planner schemas, runtime application,
logs, observations, and tests in the same slice.

### Slice 2 — canonical scoped lifecycle store

Deliver:

- explicit campaign scope resolution;
- versioned SQLite schema;
- append-only lifecycle history plus rebuildable projection;
- keep/reinforce/resolve/supersede/retract transitions;
- exact duplicate reinforcement without duplication;
- separate recalled versus reinforced timestamps;
- transactionality and foreign-key invariants;
- legacy migration, backup/preflight, rollback, idempotency, and scope honesty;
- read-only inspection/doctor commands suitable for operator audit.

Do not preserve the old table as a second live authority.

### Slice 3 — bounded recall and planner delivery

Deliver:

- deterministic tiered recall;
- protected open commitments and exact current-target constraints;
- general-memory bounds and honest omission metadata;
- continuity operation receipts in later planner context;
- a bounded elective memory search/read action or equivalent current architecture;
- observation-budget rules that preserve requested reads and decision-critical
  continuity without allowing unbounded context;
- no database write on each observation-pump tick;
- metrics and deterministic retrieval tests.

Update the planner prompt so `memories` is no longer described as the only thing
between plans if working plan outcomes and fieldbook index now also exist.

### Slice 4 — private fieldbook and open work

Deliver:

- project and entry store;
- lifecycle operations and receipts;
- one explicit active project;
- bounded automatic index only;
- elective bounded project inspection;
- planner sidecar updates with source validation;
- no arbitrary filesystem paths;
- no game-input or risk-budget effects;
- current telemetry overriding contradictory manifest/route notes;
- read-only operator inspection and optional generated export.

Prove that a project can continue across process restart without injecting every
entry into every prompt.

### Slice 5 — compaction and retrieval treatment

Deliver compaction first, then optional semantic retrieval. Keep them independent
so each can be ablated.

- compaction candidate lifecycle, dry-run, apply, rollback, and provenance;
- source history retained and projection rebuildable;
- provider/model/prompt policy receipts;
- deterministic recall remains the default/control;
- optional semantic MMR retrieval with disposable cache and explicit run metadata;
- no semantic filtering at storage time.

### Slice 6 — evaluation and strongest safe proof

Create a deterministic Ladle continuity harness and paired evaluations. It must
span multiple plans and a process/store restart.

Minimum scenario:

1. Campaign `ladle-css-01` adopts a commitment to deliver six sealed slop
   canisters and creates `delivery-001`.
2. A route attempt produces a no-op or failure outcome with a stable evidence ID.
3. A later plan records the incident without claiming delivery, updates the route
   project, and chooses a materially different method.
4. The process closes and reopens the same campaign store.
5. The next planner receives the open commitment and bounded project index, can
   electively inspect the route/delivery records, and does not repeat the failed
   action unchanged.
6. Current telemetry reports five canisters while an old fieldbook entry says six;
   current telemetry wins, and no delivery is claimed.
7. A same-named trader with a different exact entity ID does not receive the old
   entity-bound memory.
8. A different campaign receives none of Ladle's private continuity.
9. Resolving the delivery closes the commitment with cited evidence; compaction,
   when enabled, preserves the original source records and uncertainty.

Compare at least:

- current baseline/continuity disabled;
- scoped lifecycle memory enabled;
- memory plus fieldbook;
- deterministic recall versus semantic recall, if semantic retrieval exists.

Measure repeated no-ops, resumed commitments, stale-memory corrections,
unsupported success claims, cross-scope leaks, fieldbook reads, prompt size,
operation rejection rate, and restart continuity. Do not use preferred prose or
personality resemblance as the success metric.

After portable and replay evidence is green, run the strongest safe supported
integration proof. A useful live endpoint is: same explicit campaign ID across
two supported `./dev journey` processes, with the second planner demonstrably
receiving and using a grounded unresolved commitment or route lesson. Do not
claim improved gameplay competence from one run.

## Testing requirements

Run the existing baseline before editing:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

A dependency outage is not a green baseline; record it and run the strongest
available focused tests without pretending the full gate passed.

For every slice:

- write a failing behavioral test first and observe it fail;
- include success, invalid transition, unknown/missing evidence, stale identity,
  cross-campaign, rollback, and restart paths where applicable;
- test both `single_step` and `continuous` semantics;
- test PlanPatch staging, rejection, discard, and actual application;
- preserve provider-portable hosted schemas and run
  `tests/test_hosted_planner_contract.py` after any planner-model change;
- regenerate and staleness-check all JSON Schemas and generated docs;
- test observation reduction at budgets just below and above each required
  continuity surface;
- use injected clocks/ID factories where determinism matters;
- test SQLite migration against realistic legacy rows and interrupted/failed
  transactions;
- rebuild projections from canonical history and compare exact current state;
- verify deleting a derived index/cache/export changes no canonical result;
- test no input primitives and no risk-budget spend for continuity reads/writes;
- add metrics tests for every new event and receipt;
- avoid source-text assertions; exercise behavior.

Mutation testing is required on the new authority seams, not as a project-wide
ritual. At minimum target mutations that would:

- accept a foreign or nonexistent evidence ID;
- commit a stale/discarded patch's operations;
- treat recall as reinforcement;
- leak another campaign's record;
- reactivate by display name;
- allow a fact with only future evidence;
- skip a supersession/retraction status check;
- partially append without updating projection, or vice versa;
- surface all fieldbook excerpts automatically;
- treat a fieldbook manifest as inventory;
- delete compaction sources;
- let embedding availability change storage admission.

Kill those mutants or explain why a generated equivalent is non-actionable. Do
not postpone the feature until every unrelated module shard is mutated.

## Performance and observation-budget rules

- The observation pump may run around ten times per second; continuity decoration
  must not perform write transactions at that rate.
- Add indexes for campaign, status, kind, target ID, project, and ordered lifecycle
  queries. Avoid full-table scans in ordinary recall.
- Bound all automatic collections and all elective read results.
- Preserve the latest action/plan outcome, open commitments, and exact current
  target memories before optional general context.
- A deliberately requested memory or fieldbook read must either fit its documented
  bound or return typed truncation/unavailability; do not silently drop the chosen
  source.
- Log retrieval latency/candidate counts cheaply enough to diagnose regressions,
  without logging hidden reasoning.

## Planner-prompt rules

Update `prompts/planner_system.md` when the live contract changes. The prompt must
teach, without pretending semantic guarantees the runtime cannot enforce:

- world evidence is authoritative;
- working outcomes, kept memory, and fieldbook are different;
- facts/episodes cite prior/current evidence IDs;
- commitments and hypotheses retain their epistemic status;
- current target IDs are copied only from fresh observation;
- old IDs, cell labels, coordinates, and capabilities never authorize action;
- continuity operations are optional and concise;
- an invalid continuity operation may be rejected independently;
- full project content is reached for only when useful;
- finishing a plan is not automatically finishing a commitment or project;
- no unsupported success claims;
- no duplicate restatements merely to keep them visible.

Keep examples minimal and provider-portable. Do not turn a detailed Ladle example
into a universal policy that makes every agent behave like a courier.

## Documentation discipline

Follow the repository's existing hygiene rules.

- Write a concise ADR for the continuity authority model and another only when a
  later durable decision genuinely needs one. ADRs are decisions, not progress
  logs.
- Add a guide for campaign scope, migration, inspection, and evaluation if the
  operator needs a procedure.
- Generate any catalog/schema tables that restate code.
- Update `STATUS.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, config examples, and
  planner protocol docs truthfully.
- Do not create a growing engineering diary or a giant memory-design scrapbook.
- Label evidence as portable, replay/simulated, Windows integration, native
  build/load, or supervised live.

## Implementation style

- Build on current Pydantic, SQLite, runtime, observation-budget, planner-schema,
  logging, and evaluation seams. Do not create a second unintegrated agent loop.
- Prefer strict discriminated unions and runtime-owned IDs.
- Prefer explicit columns and typed payloads. Use bounded JSON only where it is an
  immutable event payload, not as an excuse to skip schema design.
- Avoid a new dependency when standard-library SQLite and current packages are
  sufficient.
- Do not preserve obsolete behavior merely because it exists in an unreleased
  schema. Preserve real user data and replay evidence through one explicit
  compatibility/migration boundary, then remove the parallel path.
- Fix the whole class of a discovered bug. If observation decoration mutates
  memory, do not merely lower the pump rate.
- Make reasonable design decisions without stopping for naming approval. Record
  genuine durable choices in an ADR and proceed.
- One invocation may touch models, store, runtime, prompt, schema, tests, config,
  metrics, and docs when that is required to finish one vertical invariant.
- Do not commit after every test tweak. Make one commit after the complete slice
  is green and reviewed.

## Per-invocation method

1. Establish repository state: branch, `git status`, recent log, current baseline,
   active config, and whether another agent has uncommitted work.
2. Read the current memory/runtime/models/prompt/observation-budget/tests plus the
   selective WorldWeaver references relevant to the next slice.
3. Classify all six queue slices as absent, partial, or complete and cite concrete
   code/tests for the first incomplete dependency.
4. State one compound slice with problem, scope, non-goals, and acceptance
   criteria. Do not ask for approval when the prompt already resolves the policy.
5. Write failing tests and watch them fail.
6. Implement the complete vertical path, including migrations, schemas, prompt,
   logs, metrics, and docs touched by the invariant.
7. Run focused tests continuously, then full pytest/Ruff/mypy and generated-artifact
   checks.
8. Run targeted mutation tests for the new authority seams.
9. Inspect the diff for live database files, run artifacts, secrets, caches,
   generated drift, unrelated churn, and accidental WorldWeaver code copying.
10. Make one intentional commit for the finished slice. Do not push or open a PR
    unless separately authorized.
11. Report what is now proven, what is only designed, what could not be run, and
    the exact next incomplete dependency.

## Required final report

```markdown
# Memory and Continuity Loop Result

## Slice completed
## Completion-matrix status
## Why this dependency came next
## Changes
## Data migration and compatibility
## Evidence
- portable
- replay/simulated
- Windows/native/live, if any
## Authority and safety review
## Performance and prompt-budget review
## Mutation results
## Not tested or not claimed
## Working-tree and commit state
## Next slice and its first failing invariant
```

## Stop conditions

Stop after the selected slice is complete, green, reviewed, and committed. Also
stop with a precise report when an actual dependency/platform failure prevents
the next safe local step, a live action needs authorization not present, or
unrelated working-tree changes make the target files unsafe.

Do not stop merely because the design is broad, the migration is difficult, or
WorldWeaver is messy. Do not skip to a prettier later phase. Deliver the strongest
complete increment at the first unmet dependency, then leave the next invariant
exact.

Begin now by establishing repository state, running the baseline, verifying the
starting defects, and selecting the first incomplete dependency-ordered slice.
