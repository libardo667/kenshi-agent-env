# Guide: campaign scope, migration, and inspection

Durable memory belongs to a **campaign** — one save lineage — and never to a config profile.

## Naming a campaign

```yaml
memory:
  enabled: true
  campaign_id: ladle-css-01   # the save lineage this profile plays
  retrieval_policy: deterministic
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

Before writing, migration backs up the prior schema as `<memory-db>.vN-backup`. Pre-campaign rows
become `keep` events under `legacy:<old-namespace>` with `legacy_unverified` authorship. Later
migrations add structured provenance and the fieldbook without inventing evidence. All are idempotent.

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

These open SQLite read-only; looking cannot change belief or register a campaign.

## Compacting active memories

Write a read-only candidate, inspect it, then apply that exact document:

```bash
uv run kenshi-agent compact-memory --campaign ladle-css-01 \
  --source mem-<first> --source mem-<second> > candidate.json
uv run kenshi-agent compact-memory --campaign ladle-css-01 \
  --apply-candidate candidate.json
```

Application rechecks all source state under one lock. Drift, edits, malformed JSON, or store failure
change nothing; success retains source history and the inspected candidate as provenance. Only
lossless compaction and deterministic recall exist. See [the compaction ADR](ADR_LOSSLESS_MEMORY_COMPACTION.md).

## What a planner is shown

Recall spends separate budgets in order:

1. open commitments;
2. memories bound to an entity in the *current* observation;
3. unresolved hypotheses;
4. general knowledge, and only this tier honours `minimum_salience`.

A record occupies one tier. `memory_recall` reports omissions; receipts report accepted, rejected,
no-op, or failed. Tight budgets preserve the latest adverse receipt, commitments, and current-target
memory. Degradation is distinct from an empty result and stops blind store retries, not gameplay.

`recall_memory` searches bounded durable memory or compact working outcomes. Its runtime-owned receipt
binds exact plan, step, source, campaign, results, and status to only the next manifest. It emits no
game input or risk-budget cost.

## Fieldbook

The fieldbook serves larger named work in the same campaign database. Its index is bounded; one
selected summary is automatic, and entries require `read_fieldbook`. Typed planner writes require
visible project IDs and appropriate delivered evidence. Self-authored notes never become world truth.

Operator inspection is read-only:

```bash
uv run kenshi-agent fieldbook --campaign ladle-css-01
uv run kenshi-agent fieldbook --campaign ladle-css-01 --project-id fbp-<id> # or --query/--markdown
```

Markdown output is disposable. Immutable evidence is resolved before grounding; non-effects may
ground a failed episode, never success. See [the evidence ADR](ADR_CONTINUITY_EVIDENCE_CAPABILITIES.md).

`memory_events` is append-only history; `memories` is its transactional, rebuildable projection.
Reads never change ordering or state. Closed records reject transitions; campaigns cannot cross.
