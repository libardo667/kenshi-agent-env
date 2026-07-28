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
campaign. Schema 2 similarly gets a `.v2-backup` before structured provenance
is added; schema 3 gets a `.v3-backup` before schema 4 adds the fieldbook. Old
event payloads remain unstructured; migration invents no evidence. These paths
are idempotent. Restore the matching backup to roll back.

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

A planner may use `recall_memory` for a bounded search of `"durable_memory"` or compact
`"working_outcomes"` outside the rich window. A runtime-owned receipt names its exact plan/step,
source, campaign, returned IDs, and `completed`, `unavailable`, or `failed` status. Its identity
and results enter only the next manifest. Unavailable and failed reads are not empty searches; the
read emits no game input or risk-budget cost.

## Fieldbook

The fieldbook shares the campaign and database but serves larger named work:
delivery dockets, route atlases, logs, plans, journals, and generic projects.
Its index is bounded; one selected summary is automatic, and entries require
`read_fieldbook`.

Planner writes use typed `fieldbook_operations`. Project IDs must occur in the
exact planner input. Observational, manifest, expense, incident, and route
entries require appropriate delivered evidence. Notes, decisions, and questions
may be self-authored but never become inventory or other world truth.

Operator inspection is read-only:

```bash
uv run kenshi-agent fieldbook --campaign ladle-css-01
uv run kenshi-agent fieldbook --campaign ladle-css-01 --project-id fbp-<id> # or --query/--markdown
```

The Markdown form is generated to stdout and disposable. Editing or deleting a
saved copy cannot change SQLite.

The runtime resolves immutable evidence before rendering grounding. Non-effects
may ground an honest failed episode, never successful world proof. The complete
capability decision is in
[the evidence ADR](ADR_CONTINUITY_EVIDENCE_CAPABILITIES.md).

`memory_events` is append-only canonical history; `memories` is its
transactional, rebuildable projection. Accepted events retain exact operation,
planner context, revisions, evidence, origin, and rendered grounding. Exact
restatement reinforces by deterministic normalized key. Reads never influence
ordering or write state. Closed records reject further transitions, and
campaigns cannot reach each other's records.
