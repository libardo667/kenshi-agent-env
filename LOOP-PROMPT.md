# Long-run gameplay hardening loop — Kenshi Agent Environment

Copy this whole document into a capable coding agent whose working directory is the repository root. Reuse
it for successive invocations. This prompt is meant to replace the older general engineering loop while
sustained practical play is the project’s main bottleneck.

Treat the current checkout, `git status`, `git log`, `STATUS.md`, generated artifacts, and the newest
complete `runs/<run-id>/` bundles as the source of truth. Never trust a protocol number, test count, queue
item, or historical claim merely because this prompt mentions it. Verify it.

One invocation closes **one observed long-run failure class**, proves the exact failing journey can proceed
beyond its former boundary, and then runs far enough to reveal the next attributable blocker. The unit of
work is a complete causal slice, not one tiny edit.

## Mission

Make the playing model practically able to use Kenshi for sustained periods: perceive, choose a meaningful
objective, execute chains of semantic actions, recover from ordinary failure, retain strategic continuity,
and keep making world-grounded progress without human steering.

The target is not merely a process that stays alive. A run that lasts 300 steps while reopening the same
screen, repeating rejected actions, changing goals without evidence, or accumulating no useful world change
is a failed run.

The development loop is:

```text
run a supervised journey long enough to expose the first real blocker
        ↓
reconstruct the earliest causal failure from append-only evidence
        ↓
name the failure class and the invariant that should have prevented it
        ↓
create a portable failing reproduction or adversarial stress test
        ↓
fix the complete class at the owning boundary
        ↓
rerun the exact same journey past the former failure point
        ↓
extend to the next soak rung and record the next blocker
```

Do not optimize for test count, line count, uptime, or affordance-catalog size. Optimize for useful
autonomous play per human intervention and per planner call.

## What “practically usable” means

Judge a long run along six axes.

1. **Progress.** The agent produces nontrivial, goal-relevant world changes or resolves uncertainty that
   changes its next meaningful choice.
2. **Liveness.** Every planner call, action, option, native command, UI interaction, recovery, and
   final-state path reaches a bounded typed terminal.
3. **Recoverability.** Ordinary model, UI, pathing, provider, and game failures lead to a fresh usable
   observation and a viable next choice rather than a dead session.
4. **Continuity.** The active objective, blocked intentions, completed work, and learned constraints survive
   plan boundaries and, where configured, process restart without becoming counterfeit world truth.
5. **Efficiency.** Replans, inspections, rejected actions, no-ops, and hosted calls remain proportionate to
   useful action rather than dominating the run.
6. **Honesty.** Portable, replay, Windows, native build/load, supervised-live, and repeated-live evidence
   remain explicitly distinct.

Do not count these as progress by themselves:

- a plan being accepted;
- a planner call succeeding;
- opening and closing an unchanged interface;
- repeating an inspection whose result cannot affect a decision;
- a rejected, not-executed, no-op, or unknown action outcome;
- waiting that is not part of an option which later reaches a meaningful terminal;
- surviving until the step cap.

Prefer scenario milestones and verified state deltas over a universal scalar “intelligence score.” Money,
inventory, nutrition, position, task state, dialogue state, UI cleanliness, and completed commitments are
useful only when relevant to the journey’s actual objective.

## Starting procedure

1. Establish repository state: branch, status, recent commits, changed files, and whether another agent left
   work in progress. Do not overwrite unrelated edits.
2. Read `STATUS.md`, `ARCHITECTURE.md`, `SECURITY_AND_SAFETY.md`, the active live profile,
   `prompts/planner_system.md`, generated action/affordance docs, relevant ADRs, and the newest run bundles.
   Treat the old memory-continuity loop as historical unless a current run exposes a continuity defect.
3. Run the portable baseline: `uv run pytest -q`; `uv run ruff check .`; `uv run mypy src`. If an
   environment-specific dependency prevents the normal command, identify that exactly and run the strongest
   honest subset; do not relabel it as the full gate.
4. Before live work, run `./dev launch --preflight-only`. Use `./dev play`, `./dev journey`,
   `./dev scenario`, `./dev recover`, and `./dev crash` rather than direct Python, raw request files,
   manual SendInput snippets, or PTYs.
5. Give every live run a descriptive unique run ID. Preserve its raw bundle. Never edit old run evidence in
   place.

## Build the failure dossier first

For the newest relevant long run, identify:

- run ID, exact profile, planner/provider/model, scenario or campaign, start state, objective, and requested
  step budget;
- the useful milestones actually reached;
- the first step, timestamp, or world revision where useful progress ceased;
- the earliest causal failure, not merely the final stop reason;
- the exact planner output, action, option, UI state, telemetry state, receipt, or host event involved;
- whether the agent noticed the problem, attempted recovery, repeated it, or changed goals;
- human input or intervention and why it became necessary;
- final game, UI, native-command, ownership, and pause/speed state;
- the smallest invariant that would have made the failure impossible or recoverable.

Choose the **earliest causal blocker**. A later schema error, safety stop, or bad plan may be downstream of
an observation loss, stale UI ownership, provider truncation, or missing planner feedback.

If the evidence cannot distinguish plausible causes, add the smallest missing instrumentation and use it
immediately in the same invocation. Do not stop at “better logging” when one more focused run can use that
logging to close the blocker.

Prefer deriving metrics from existing append-only events. Add new runtime state only when the evidence
cannot be reconstructed honestly from the log.

## Long-run failure taxonomy

Classify the blocker before changing code.

- **Launcher/configuration authority:** the supported command silently overrides the selected profile, drops
  an option, or runs a different planner/budget than reported.
- **Hosted planner transport:** timeout, truncation, empty content, provider routing, unsupported schema,
  malformed JSON, SDK mismatch, or retry policy.
- **Planner contract/context:** wrong output shape, stale patch, missing IDs, oversized recurring
  prompt/schema, context budgeting, or feedback omission.
- **Strategic continuity:** goal churn, forgotten blocked intention, repeated failed branch, unjustified
  commitment closure, or plan-local thinking that cannot sustain a journey.
- **Observation/world model:** missing, stale, over-budgeted, contradictory, or misinterpreted evidence;
  entity or window identity loss.
- **Action contract:** bad binding, already-satisfied action, ambiguous completion, wrong target, no causal
  terminal, or a mechanical chain exposed at the wrong abstraction level.
- **UI liveness:** stranded modal/window stack, inaccessible world view, cursor desynchronization, repeated
  open/close cycle, or no bounded route back to a usable state.
- **Executor/concurrency:** stale result publication, unmatched plan revision, cancellation race, budget
  leak, final-state ownership conflict, or a terminal that does not compose over many plans.
- **Native/host stability:** command transit, telemetry stall, identity change, renderer/device reset,
  launcher failure, crash reporter, or recovery failure.
- **Continuity subsystem:** retrieval, campaign isolation, store quarantine, restart delivery, or
  evidence-capability error.
- **Missing affordance:** the desired gameplay intention is grounded and valid, but no current semantic
  action can express it.

Do not call every failed intention a missing affordance. A present action that cannot be selected, bound,
completed, recovered, or remembered is an existing surface defect.

## Progressive soak ladder

Use fixed, reproducible starts whenever possible. The default live ladder is:

```text
portable/replay reproduction
        ↓
30-step supervised journey
        ↓
75-step supervised journey
        ↓
150-step supervised journey
        ↓
300-step supervised journey
```

Skip rungs already proven by relevant current evidence. `--steps` is a ceiling, not a success criterion. A
rung passes only if it contains sustained useful play or completes its objective cleanly.

After a fix, rerun the **same scenario, objective class, planner route, and relevant configuration** first.
It must pass the former blocker and reach either its next semantic milestone or materially beyond the former
useful-action boundary. Do not switch to an easier seed and call that a regression proof.

Only then advance one rung. When the longer run exposes a new unrelated blocker, preserve it as the next
slice rather than widening the current patch indefinitely. A safety, launcher-truth, evidence-corruption, or
final-state defect preempts the current slice because all later evidence depends on it.

## Long-run scoreboard

`kenshi-agent summarize` should eventually make these visible when the log contains the evidence. Add them
incrementally when they are needed to attribute real runs, not as a speculative dashboard project:

- action outcomes by `changed`, `no_op`, `unknown`, and `not_executed`;
- executed/rejected actions by semantic kind and target;
- normalized repeated action signatures and longest no-progress streak;
- useful-action streak before the first hard blocker;
- plan completion, abort, patch, rejection, and replan counts;
- hosted planner failures by attributable class, including truncation;
- planner latency and action throughput;
- active-goal or commitment revisions and unjustified goal churn where reconstructible;
- UI/world usability stalls, purposeless paused/running time, and cleanup state;
- human handoffs, takeover attempts, F12 stops, and interventions;
- relevant initial-to-final scenario deltas;
- exact stop reason plus the earliest blocker classification.

Do not manufacture a “useful action” label from visual change alone. Prefer controller-verified semantic
terminals and scenario-specific milestones; report ambiguous progress as ambiguous.

## Current verified candidates — inspect before choosing

These were present in the reviewed checkout. They are candidates, not eternal facts. Verify each against
current code and newer run evidence.

1. Only decision-relevant change now counts as progress; displacement, controller pause/speed, camera
   bearing, and `already_clear` recovery are `no_op`. `live-trade-surface-20260729-r1` re-derives from
   11 changed/1 no-op to 6/6 with a six-action no-progress streak. `summarize` still cannot aggregate
   assessments, repeated signatures, or that streak. Portable-tested only.
2. Prompt cost, over 230 hosted requests in ten runs: median 116-137 KB and 35-42k tokens, split system
   40.9-41.9 KB (~32%), schema 39.6 KB (~31%), observation 30-48 KB (~30%). `schema_in_prompt` was true
   230/230 - the constrained route never survives and the latch is permanent. In the schema, 62 `$defs`
   are 90.5% of bytes, 14.2 KB is description prose, and 22 of 24 action kinds are also in
   `planner_system.md`, duplicating 7.4 KB. The lever is not compression: ~81 KB is a byte-identical
   prefix already first in the message and nothing sets `cache_control`. Cache and de-duplicate before
   shortening generated descriptions; measure ablation before cutting anything.
3. Different characters and save lineages have shared one configured `fresh-funded-solo` campaign, so the
   journal carries earlier characters, recruitment, purchases, and open commitments across a fresh start.
   Personal continuity must not cross one; reusable play knowledge needs a separate honest home.
4. Trade authority took four commits because native exports conclusions, not facts: Kenshi keys a shop
   window by its `ShopTrader` object, the exporter probed the owner Character, and `ui.active_screen`
   collapsed a real trade to `inventory`. Native now probes the object and the guards use window
   ownership, both portable-tested. Python still matches captions, which cannot separate a merchant's
   shop window from his equipment window. Export per-window owner identity and kind, then delete it.
5. `plan purchase risk budget exceeds configured maximum` never names the maximum, so the model replans
   instead of satisfying it; every plan-validation error should carry the value that would pass.
6. `shop_inventory_owner` appears zero times in the `r2` and `r5` logs, so the field the purchase binding
   and trade predicate both depend on is absent from run evidence, and a purchase failure cannot be
   attributed from a bundle alone.
7. `PlanStep` carries no intent, so every continuous `ActionOutcome` records "Execute plan X step Y" as
   its purpose - the exact non-purpose `PlanOutcome` was created to prevent.

## Fix the class, not the final symptom

Write the failing test first and observe it fail for the intended reason. Extract a minimal replay or
fixture from the run where useful, but do not hardcode one transcript as gameplay policy.

For each defect, state:

- owning boundary;
- violated invariant;
- why existing tests stayed green;
- portable reproduction;
- failure, cancellation, and unknown-state behavior;
- exact live acceptance criterion.

Normalize failure signatures before applying “identical failure” limits when provider request IDs,
timestamps, or incidental wording could make the same semantic error look different.

A transient bad planner response is usually recoverable. Bound retries and feed the precise rejection back
to the next call. Do not dispatch stale authority, relax validation, infer success, or retry an at-most-once
world command whose delivery is uncertain.

Every supported UI state reached by an agent action must have one of two honest outcomes: a bounded
controller-owned route back to a usable world state, or a typed terminal explaining why no safe route
exists. Repeatedly pressing Escape or reopening windows is not a recovery design.

Anti-loop behavior must outlive the shortest recent-history window when the same blocked intention can recur
later. Prefer runtime-owned normalized outcome and blocker identity over asking the model to remember every
prior mistake in prose.

## Semantic-option rule

When long runs repeatedly fail on the same brittle mechanical sequence, promote that sequence into a
controller-owned semantic option **only when the strategic choice remains with the model**.

The model should choose things such as goal, actor, target, destination, quantity, item category, acceptable
risk, and when to stop. The controller may own exact window routing, speed changes, retries that are
mechanically safe, causal verification, cleanup, and restoration.

`harvest_resource` is the pattern: “harvest this bounded quantity from this exact resource,” not a scripted
economic strategy. Extend that pattern where evidence supports it. Do not hide exploration, target choice,
purchasing priorities, combat strategy, or goal selection in deterministic code merely to make a demo pass.

## Playing-model prompt and context discipline

The playing model needs the current semantic affordances, relevant preconditions, result vocabulary, current
authority, active plan, recent outcomes, and selected durable context. It does not need to reread every
implementation invariant the runtime already enforces.

Do not blindly shorten the prompt. First measure:

- system, observation, schema, screenshot, and total request size;
- constrained versus schema-in-prompt route;
- response length, truncation, validation failure, and latency;
- which omitted sections actually change valid action selection.

Preserve exact IDs, current-observation precedence, safety semantics, uncertain at-most-once outcomes, and
active-plan patch rules. Prefer generated compact action summaries over hand-maintained duplicated prose.

The current prompt’s final instruction that a rejected action “wastes a decision cycle, so remain
conservative” may discourage useful exploration. Change it only if run evidence shows avoidant planning, and
replace it with grounded risk-aware experimentation rather than indiscriminate action.

## Gameplay proving ground

The central competence milestone is a repeated, model-authored town-local survival/economic loop:

```text
assess current state and needs
        ↓
choose a grounded resource or other income opportunity
        ↓
acquire a bounded useful quantity
        ↓
reach and use a trader
        ↓
sell intentionally and verify money/inventory conservation
        ↓
buy food when the current state warrants it
        ↓
leave the interface usable and continue toward a broader objective
```

Do not script that chain into a test planner. The playing model chooses the goal, resource, quantity,
trader, sale, and need response from current evidence. The runtime owns brittle mechanics and causal proof.

Initial acceptance is three completed economic cycles from a fixture-attested start with no more than one
human intervention. Follow with one real process restart that resumes an open evidence-grounded commitment
without replaying completed work or claiming unobserved success. These are directional milestones; a more
fundamental earlier blocker remains the next slice.

## Supervised live authorization

Use of this prompt carries standing authorization, while the operator is present, for ordinary Kenshi
gameplay on project-owned authored starts and explicitly designated disposable test saves. Do not ask
separately before spending or selling in-game money, transferring/equipping/dropping inventory, travel,
dialogue, work, sleep/healing, recruitment/dismissal, theft, combat, retreat, injury, capture, death, or
overwriting a reserved project test-save slot. These are game consequences, not real-world hazards.

Keep the normal execution gates, F12 brake, human-input handoff, current focus checks, causal
pause/recovery, and supported launcher path. State the intended live chain and stop condition briefly before
execution.

This authorization does **not** cover non-project saves, arbitrary input outside Kenshi and repository
tooling, disabling safety gates to make a test pass, accessing secrets or external accounts, changing
unrelated system settings, publishing/pushing/opening a pull request, or destructive host operations.

Manual gameplay may establish a fixture or diagnose Kenshi, but it is not agent capability evidence. Only
the supported path and its artifacts prove the agent.

## Development pace

One bounded slice may and often should touch several connected modules, tests, configuration fields,
generated artifacts, and one live acceptance run. Do not split one causal fix into a procession of
micro-adjustment commits.

Use focused tests while iterating. Run the full gates once the complete slice is assembled. Prefer one
intentional commit for the finished slice when commits are part of the active workflow; never commit every
minor edit merely to feel safe.

Do not build a parallel framework, broad ontology, new memory subsystem, or universal game abstraction
unless the current blocker requires it. Do not resume a repository-wide mutation campaign while practical
play is blocked. Focused mutation or adversarial substitution is useful for the changed boundary; broad
rollout follows sustained gameplay competence.

Do not stop after a portable fix when supervised acceptance is safe and required to prove the issue.
Equally, do not call one successful live run generalization.

## Per-invocation method

1. Establish state and run the baseline.
2. Build the newest failure dossier.
3. Select one earliest causal failure class and state scope, non-goals, and acceptance criteria.
4. Add the failing portable reproduction and observe red.
5. Implement the smallest complete class-level fix at the owning boundary.
6. Run focused tests; challenge the test by temporarily breaking or reversing the new behavior.
7. Run full portable gates, generated-artifact checks, doctor/preflight, and any relevant native build/load
   checks.
8. Rerun the exact live scenario past its former blocker.
9. Advance one soak rung or complete the next semantic milestone.
10. Preserve the run bundle, summarize the new blocker, inspect final state, and run supported recovery
    after any abnormal exit.
11. Inspect the diff for secrets, run artifacts, binaries, temporary payloads, stale generated files, and
    unrelated churn.
12. Stop. Leave the next invocation one precise failing invariant, not a vague aspiration.

## Required final report

```markdown
# Long-run Gameplay Loop Result

## Failure dossier
## Failure class and violated invariant
## Slice completed
## Changes
## Portable evidence
## Windows/native evidence
## Supervised-live regression run
## Progress and liveness scoreboard
## Former failure boundary and how far the rerun passed it
## Safety, final-state, and recovery review
## Not tested
## Working-tree state
## Next blocker and its failing invariant
```

Label every claim as portable, simulated/replay, Windows, native build/load, supervised live, or repeated
live. Include run IDs for live claims.

## Stop conditions

Stop after the selected class is fixed, all applicable gates are green, the exact regression journey has
passed its former boundary, and the next blocker is attributable.

Stop earlier only when continuing would require non-project save access, unapproved host/external action,
unavailable operator supervision for live input, untrustworthy telemetry/native state, unresolved unrelated
edits in target files, or a platform failure that prevents an honest test. In that case, still leave the
strongest portable increment and exact unblock condition.

Do not stop because the next useful run is longer, the bug crosses modules, or a cohesive fix is larger than
a micro-patch. Do not claim success merely because a step cap was reached.

Begin by establishing repository state, inspecting the newest long run, and choosing the earliest causal
blocker. If no relevant run exists, start with the smallest unproven soak rung on a disposable fixture and
let actual play select the slice.
