# ADR: Continuity authority and commit timing

## Status

Accepted.

## Context

The agent had one place to put anything it wanted to keep — a SQLite table of
memories — and no way to tell four different kinds of knowing apart. What the
game proved, what the agent had just tried, what it had chosen to remember, and
what it merely intended all arrived in the same list, with the same weight, and
with grounding the model wrote for itself in a free-text `evidence` string.

Four consequences followed, each observed in code rather than in theory.

1. **Continuity could describe a future.** A continuous plan's `memory_writes`
   were committed immediately after validation and before any step executed.
   The runtime also wrote an automatic `Set out to: <objective>` *episode* —
   filing an intention under the kind reserved for events that happened. A plan
   could therefore leave behind a durable record of work it never did.
2. **Grounding was unchecked.** `evidence` was free text. A write could name an
   outcome the receipt did not prove, and nothing compared the two.
3. **Reading was reinforcement.** `recall()` opened a write transaction to
   refresh `last_accessed_at`, and the recall ordering then read that same
   column back. The observation pump decorates roughly ten observations a
   second, so merely looking at the world reordered memory by how often it had
   recently been looked at.
4. **A model-authored field was dead.** `PlanPatch.memory_writes` existed in the
   schema, was offered to every planner, and was committed nowhere.

There was also no stable identity for anything a planner might cite. Action
outcomes had no ID and no plan/step provenance, and a finished plan left no
record of what it had set out to do — so the next planner reconstructed purpose
from `Execute plan X step Y`.

## Decision

Three authorities, named and kept apart.

**World evidence** — telemetry, receipts, revisions, controller-owned semantic
evidence — is the only thing that establishes what the game did. Unchanged.

**Working continuity** is runtime-owned, bounded, and run-scoped, and lives in
`ContinuityLedger`. It issues `ao-<n>` for every action outcome and `po-<n>` for
every plan outcome, both with full provenance: run, plan, version, step,
command, action-start and completion revisions, timestamps. A plan outcome
carries the plan's *original objective* alongside its disposition
(`completed`, `failed`, `abandoned`, `terminated`), reason, completed step IDs,
and terminal revision. The visible window is bounded; issuance alone is not
citation authority after an item leaves the planner's input.

Every planner call pairs its final input and parsed output with an immutable
`PlannerContextManifest`: the authored revision and only the current entities,
outcomes, memories, and advisor briefs actually delivered. That means final
budgeted JSON for hosted planners, full observations for in-process and
subprocess planners, and no observation IDs for a script consuming only its
file. Validation through patch application carries this exact context; a later
commit revision cannot rewrite it.

**Durable kept memory** belongs to an explicit **campaign** — one save lineage,
never a config profile name. A live run with memory enabled and no campaign
fails closed rather than sharing a `default` namespace across unrelated saves;
`ephemeral: true` is the explicit opt-out; an attested scenario derives a
deterministic campaign from its exact `save_id`.

It is reached through exactly one path, `ContinuityAuthority.apply`. Plans,
single-step decisions, and applied patches all go through it, so the rules are
stated once:

- A `fact` or an `episode` must cite at least one `EvidenceReference` — a
  discriminated set of `current_observation`, `action_outcome`, `plan_outcome`,
  `memory`, and `advisor_brief`. Each is validated against the authority that
  owns it. An advisor brief renders as advice, never as observation.
- A `commitment` or a `hypothesis` may be self-authored, but if it does cite a
  reference, that reference must still resolve.
- A `target_id` must name an entity in the authored input's fresh, world-facing
  observation fields. Remembered text and stale telemetry grant no authority.
- The stored `evidence` string is rendered by the runtime from the references
  that resolved. There is no free-text branch, so a record cannot describe
  proof it does not have.
- Every operation produces an accepted / rejected / no-op receipt. Rejection is
  per-operation: an invalid one beside a valid one takes only itself down, and
  an otherwise valid game plan still executes.

Commit timing is exact. A plan's operations are processed only after schema,
causal-basis, assumption, control-mode, graph, and budget validation pass; a
rejected plan contributes nothing. A single-step decision's operations are
processed after its action receipt. A patch's operations are committed at the
one point the patch is revalidated and becomes the active plan — a staged patch
that is rejected, superseded, or discarded contributes nothing.

The automatic `Set out to:` episode is gone. Plan purpose is working history
now: it is recorded by `_record_plan_outcome` once the plan has ended, with the
reason it ended.

Recall is read-only and ordered by `salience, created_at, id`, never read time.
`record_delivery` marks only records in the final planner-context manifest.

The versioned store pairs append-only `memory_events` with a transactionally
written, rebuildable `memories` projection.
All five transitions have separate `reinforced_at`,
`resolved_at`, `superseded_at`, and `last_delivered_at` timestamps. A closed
record refuses every further transition, exact restatement reinforces by a
deterministic normalized key rather than duplicating, and no campaign's
operations can reach another's records.

## Consequences

- A planner physically cannot cite the success of its own future steps: the
  identity it would need does not exist until after the action is assessed.
- `memory_writes` is renamed `continuity_operations` on `PlannerDecision`,
  `PlanEnvelope`, and `PlanPatch`, tagged by transition.
- Migration copies the database before any write, is idempotent, and keeps
  pre-campaign rows under `legacy:<namespace>` with `legacy_unverified`
  authorship. They predate grounding, and handing them to whichever campaign
  opens the file next would give one playthrough another's beliefs. Promoting
  them is a human judgment; there is no automatic path.
- The next observation surfaces bounded receipts with authored and commit revisions.
- Procedures: [GUIDE_CAMPAIGN_CONTINUITY](GUIDE_CAMPAIGN_CONTINUITY.md).
