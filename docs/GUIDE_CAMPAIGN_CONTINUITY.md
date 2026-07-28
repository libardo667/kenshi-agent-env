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

A live run with durable memory enabled and no campaign identity fails before the
run directory is used. That is deliberate: the alternative is one shared
namespace, which is how two unrelated saves came to read each other's memories.
The resolved campaign and its origin are logged as `campaign_scope` at run start.

A scenario campaign is derived from the exact `save_id`, so repeat runs of one
fixture accumulate while a different save stays separate.

## Migrating an existing database

The first open of a pre-campaign database migrates it in place:

1. the file is copied to `<memory-db>.v1-backup` **before any write**, including
   the journal-mode switch, so the backup is byte-for-byte what you had;
2. every row becomes a `keep` event plus a projection row;
3. rows keep `legacy_unverified` authorship — they predate grounding, and
   nothing has checked them;
4. rows land in `legacy:<old-namespace>`, not in whatever campaign opened the
   file. Assigning them to a live campaign would hand one playthrough's beliefs
   to another.

Migration is idempotent: reopening does not re-run it or duplicate anything. To
roll back, stop the agent and restore the `.v1-backup` file.

To read migrated rows, name their campaign explicitly:

```bash
uv run kenshi-agent memory --campaign legacy:live-longform
```

Deciding they belong to a real campaign is a human judgment. There is no
automatic promotion, because the store cannot tell which save they came from.

## Inspecting a store

```bash
uv run kenshi-agent memory                          # every campaign
uv run kenshi-agent memory --campaign ladle-css-01  # active records
uv run kenshi-agent memory --campaign ladle-css-01 --memory-id mem-<id>
```

Every one of these opens SQLite read-only. Looking cannot change what an agent
believes, and cannot register a campaign that was never played.

## What a planner is shown

Recall is tiered and each tier has its own budget, so the loudest tier cannot
eat the others:

1. open commitments;
2. memories bound to an entity in the *current* observation;
3. unresolved hypotheses;
4. general knowledge, and only this tier honours `minimum_salience`.

A record belongs to exactly one tier. What was left out is reported in the
observation's `memory_recall`, so a planner can tell "nothing else exists" from
"more exists, not shown". `recent_continuity_receipts` carries the last few
accepted/rejected operations so one deterministic mistake is not repeated.

Open commitments and current-target memories also survive payload budgeting;
general context is what a character budget is for.

A planner may reach past automatic recall with the `recall_memory` action: a
bounded literal substring search whose typed result arrives in `memory_search`
on the next call only. It emits no game input and spends no pointer, purchase,
or native risk budget — it costs one plan action, because deliberation is not
free, and nothing else.

## What the store guarantees

- `memory_events` is append-only. Nothing is deleted or rewritten; resolution,
  supersession, retraction, and delivery are all explicit rows.
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
