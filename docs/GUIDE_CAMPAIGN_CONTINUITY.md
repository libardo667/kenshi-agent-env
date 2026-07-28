# Guide: campaign scope, migration, and inspection

Durable memory belongs to a **campaign** — one save lineage — and never to a
config profile. This guide covers naming one, migrating an existing database,
and auditing what an agent believes.

## Naming a campaign

```yaml
memory:
  enabled: true
  campaign_id: ladle-css-01   # the save lineage this profile plays
```

Or, when the run's memories should not outlive it:

```yaml
memory:
  enabled: true
  ephemeral: true             # scoped to `run:<run-id>` and never reused
```

The two are mutually exclusive. Resolution order:

| Situation | Campaign | Origin |
| --- | --- | --- |
| `campaign_id` set | that value | `configured` |
| `ephemeral: true` | `run:<run-id>` | `ephemeral` |
| attested scenario, no campaign | `scenario:<scenario-id>:<save-id>` | `scenario` |
| mock or replay, nothing set | `run:<run-id>` | `ephemeral` |
| **live, nothing set** | **refused** | — |

A live run with memory enabled and no campaign fails before creating its run
directory. Scenario campaigns use the exact `save_id`; the resolved campaign
and origin are logged as `campaign_scope`.

## Migrating an existing database

On first open, a pre-campaign database is copied to
`<memory-db>.v1-backup` before any write. Rows become `keep` events under
`legacy:<old-namespace>` with `legacy_unverified` authorship, never the opening
campaign. Schema 2 similarly gets a `.v2-backup` before schema 3 adds structured
provenance. Old event payloads remain unstructured; migration invents no
evidence. Both paths are idempotent. Restore the matching backup to roll back.

To read migrated rows, name their campaign explicitly:

```bash
uv run kenshi-agent memory --campaign legacy:live-longform
```

Promotion is manual because the store cannot infer which save produced them.

## Inspecting a store

```bash
uv run kenshi-agent memory                          # every campaign
uv run kenshi-agent memory --campaign ladle-css-01  # active records
uv run kenshi-agent memory --campaign ladle-css-01 --memory-id mem-<id>
```

Every one of these opens SQLite read-only. Looking cannot change what an agent
believes, and cannot register a campaign that was never played.

## What a planner is shown

Recall spends separate budgets in order:

1. open commitments;
2. memories bound to an entity in the *current* observation;
3. unresolved hypotheses;
4. general knowledge, and only this tier honours `minimum_salience`.

A record occupies one tier. `memory_recall` reports omissions; identified
`recent_continuity_receipts` report accepted, rejected, no-op, or failed. Fix a
rejection; failure quarantines writes. Tight budgets preserve the latest adverse
receipt, while open commitments and current-target memories also survive.
Persistent read/write degradation reasons distinguish quarantine from an empty
result; read failure disables both paths without stopping play or blind retries.

A planner may use `recall_memory` with `source: "durable_memory"` for a bounded
literal record search, or `"working_outcomes"` for compact run-local digests
outside the rich window. Results reach the next call only; only returned IDs
enter its manifest. The read emits no game input or risk-budget cost.

## What evidence may establish

The runtime resolves each reference to a typed immutable snapshot, validates
what its authority can establish, and only then renders grounding:

| Operation claim | Required capability |
| --- | --- |
| fact | fresh observation, controller-verified world effect, or causally observed change |
| episode | observation, action attempt, or plan lifecycle outcome |
| open commitment or hypothesis | may be self-authored |
| resolve commitment | fresh or causally verified world evidence |
| resolve hypothesis | observed evidence plus `confirmed`, `rejected`, or `unknown` |

Non-effect evidence may ground an honest failed episode, never successful world
proof. See [the evidence ADR](ADR_CONTINUITY_EVIDENCE_CAPABILITIES.md).

## What the store guarantees

- `memory_events` is append-only. Nothing is deleted or rewritten; resolution,
  supersession, retraction, and delivery are all explicit rows.
- Accepted lifecycle events retain the exact operation, authored context,
  authored and commit revisions, references, typed resolved evidence snapshots,
  plan/step origin, and rendered grounding. The projection exposes the latest
  provenance; full history remains authoritative.
- `memories` is a projection, updated in the same transaction that appends the
  event. If the two ever disagree, the history wins and `rebuild_projection()`
  restores agreement.
- Recall reads only. Ordering uses declared salience and creation time, never
  read time.
- Exact restatement reinforces rather than duplicates, by a deterministic
  normalized key — kind, squashed whitespace, case, and exact target. The
  storage boundary makes no similarity judgment.
- A closed record refuses every further transition, and one campaign's
  operations cannot reach another's records.
- Recall and search both write nothing, at any rate.
