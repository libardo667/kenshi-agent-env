# ADR: concurrent models propose future intent

Status: accepted

## Context

Concurrent hosted planning asked a language model to author `PlanPatch` while a
monitored option and the world kept moving. The patch required exact plan and
step IDs, plan version, world revision, graph replacement, and guarded
interruption mechanics. In one live soak, all ten advisories were rejected or
discarded while nine of the ten primary gameplay options succeeded.

This supersedes the hosted-model patch-authoring portions of
[hosted plan proposals](ADR_HOSTED_PLAN_PROPOSALS.md),
[bounded continuous planning](ADR_CONTINUOUS_PLANNING.md), and
[active-option interruption](ADR_ACTIVE_OPTION_INTERRUPTION.md). `PlanPatch`
remains the executor's strict optimistic-concurrency type, and deterministic or
scripted planners may still construct it directly.

## Decision

A hosted continuous planner always authors `PlanProposal`. With no active plan,
the runtime compiles it into `PlanEnvelope`. With `ActivePlanContext`, the
runtime compiles the same future intent into a non-interrupting `PlanPatch`.

The compiler copies plan identity, version, and immutable revision; allocates
step IDs disjoint from active and completed steps; derives the future graph,
conditions, budgets, and sidecars; and leaves `interrupt_active_step_id` null.
The model cannot author graph identity or interruption mechanics.

The immutable revision proves which observation the proposal was authored
from; it does not require telemetry to freeze while the model answers. The
executor deterministically constructs a candidate on current state, requires
the entry action to retain current authority, and repeats latest-state and
budget validation after the active option reaches its terminal. Changed or
missing references still fail closed; an advancing sequence alone is not a
rejection reason.

Urgent cancellation remains owned by deterministic safety reflexes. A future
model-facing strategic interruption, if needed, requires its own small intent
contract and runtime-compiled pause handoff; it must not expose raw patch graph
mechanics again.

## Consequences

- Concurrent deliberation can still overlap genuinely long options and stage
  useful future play.
- Model latency may still invalidate a referenced action or outlast its active
  option; latest-state validation rejects the former, and the scheduler avoids
  calls that cannot affect the latter.
- Patch validation remains strict, but failures now attribute runtime/compiler
  defects rather than model-authored bookkeeping.
