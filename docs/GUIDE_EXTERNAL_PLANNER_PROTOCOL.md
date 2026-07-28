# External planner protocol

The subprocess adapter starts one isolated child process per decision.

## Request

The child's stdin receives exactly one UTF-8 JSON line containing the complete
`Observation` schema. `screenshot_path` refers to a local file available to the
child process. The runtime records this subprocess call as a `full_observation`
planner context before launch; if the child fails, that attempted input remains
diagnosable without becoming a successful decision.

## Response

The child writes one JSON object to stdout and exits with code zero:

- `planning_mode: single_step` requires `PlannerDecision`.
- `planning_mode: continuous` without `active_plan` requires a bounded
  `PlanEnvelope` tied to the observation's exact `world_revision`.
- `planning_mode: continuous` with `active_plan` requires a `PlanPatch` tied to
  that exact plan, version, active step, and world revision.

Diagnostic logs belong on stderr. If several stdout lines are written, the
runtime parses the final non-empty line.

Example:

```json
{
  "intent": "Pause before resolving the threat",
  "rationale": "A visible hostile is within 25 units and the game is unpaused.",
  "action": {"kind": "pause", "paused": true},
  "confidence": 0.96,
  "expected_observation": "The next telemetry snapshot should report paused=true.",
  "continuity_operations": [],
  "fieldbook_operations": []
}
```

The rationale must be a concise decision basis, not private chain-of-thought.

`continuity_operations` is the one way to change durable memory, and it is
optional everywhere it appears — on decisions, plans, and patches alike.
Operations are `keep`, `reinforce`, `resolve`, `supersede`, and `retract`; kept
records are `fact`, `episode`, `commitment`, or `hypothesis`. A fact or episode
must cite at least one entry in `references`, and every reference must be
present in this exact request: `outcome_id` from
`recent_action_outcomes`, `plan_outcome_id` from `recent_plan_outcomes`, a
memory `memory_id`, an advisor `brief_id`, or `{"source":
"current_observation"}`. Issuance elsewhere in the run is not enough. The
current observation reference is permanently bound to this request's
`world_revision`; a later commit observation cannot rewrite it. A `target_id`
must come from a fresh world-facing entity in this request, never remembered
text. There is no free-text evidence field: the runtime renders grounding from
resolved references. One invalid operation is rejected with a runtime-identified
typed receipt while the surrounding decision or plan still executes. An
unexpected store failure is `failed`, rolls back, and quarantines later
continuity writes for that run. The next input carries a bounded receipt digest
so the exact rejected operation can be corrected rather than repeated.

Reference existence is not evidence capability. Facts require a fresh
observation, controller-verified world effect, or causally observed change.
Episodes may record failed, no-op, not-executed, unknown, and plan outcomes, but
retain that exact status. Advice, remembered belief, and plan completion cannot
alone establish a world fact. `resolve` always requires references and accepts
only an active commitment or hypothesis; facts and episodes use `supersede` or
`retract`. A commitment cannot close on advice, belief, plan disposition,
no-op, not-executed, or unknown evidence. Hypothesis resolution supplies
`disposition: "confirmed" | "rejected" | "unknown"`.

`recall_memory` defaults to `source: "durable_memory"`. Setting
`source: "working_outcomes"` performs a bounded read of compact run-local action
and plan digests. The next request carries one runtime-owned `memory_search`
receipt with an exact `receipt_id`, plan/step provenance, source, campaign
scope, result IDs, and `completed`, `unavailable`, or `failed` status. Its
receipt and result IDs enter only that request's planner manifest, allowing
deliberate citation without placing all run history in automatic context.
Unavailable and failed reads are not evidence of an empty search and should
not be blindly retried.

`fieldbook_operations` is the planner's typed route into larger campaign-scoped
work: named delivery dockets, route atlases, incident logs, vendor ledgers,
equipment plans, journals, and generic projects. A bounded project index and
one selected active summary may appear automatically. Use the zero-input
`read_fieldbook` action to retrieve entries for one exact visible project ID or
a bounded literal query; its runtime-owned receipt and results appear in
exactly the next request. Observations, manifests, expenses, incidents, and
route entries require suitable references from the exact authored context.
Notes, decisions, and questions may be self-authored, but never override
current telemetry, inventory, or controller-owned facts.

Commit timing is exact. A plan's operations are processed after the plan passes
every validation gate; a decision's after its action receipt; a patch's only if
that exact patch is revalidated and becomes the active plan. This timing
applies to continuity and fieldbook operations alike. A rejected plan or a
discarded patch writes nothing.

For continuous output, use `schemas/plan.schema.json` or
`schemas/plan_patch.schema.json` according to `active_plan`. Every plan is
bounded and acyclic, binds its control mode and causal revision, declares typed
assumptions, preconditions and postconditions, and carries action, wall-clock,
game-time, and risk budgets. During a monitored option, a patch may change only
future steps unless the exact active step opts into interruption and the
replacement begins with a causally confirmed pause handoff. The executor—not
the child process—owns active state, retries, branches, budget accounting,
condition evaluation, cancellation, and postcondition polling. A snapshot at or
before the action-start revision cannot confirm success. A later snapshot
satisfies only the temporal fence; most postconditions are still
planner-authored and are not controller-owned proof that the intended action
caused the observed change.

## Errors

A timeout, non-zero exit code, empty stdout, or schema violation becomes a
planner error. The runtime records it. Single-step mode produces a stop
decision; continuous mode requests a fresh plan until
`max_consecutive_replans` is exhausted. It never repairs arbitrary output
silently.
