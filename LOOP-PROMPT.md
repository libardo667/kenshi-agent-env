# Kenshi long-run engineering loop

Copy this document into a capable coding agent working at the repository root.
It is the durable method half of the loop. It deliberately contains no current
blocker list, protocol number, test count, or run claim.

Derive current state at the start of every invocation from the checkout,
generated artifacts, installed-game parity checks, and append-only run bundles.
Do not add a newly discovered blocker to this prompt.

## Mission

Make the playing model practically able to use Kenshi for sustained periods:
perceive the world, choose meaningful objectives, execute semantic action
chains, recover from ordinary failure, retain strategic continuity, and keep
making world-grounded progress without human steering.

Uptime is not success. Reopening the same screen, repeating rejected actions,
changing goals without evidence, or surviving to a step cap without useful
world change is a failed run.

Optimize for useful autonomous play per human intervention and planner call,
not test count, line count, catalog size, or the number of accepted plans.

## Two queues, both eligible

Every invocation considers two independent sources of work:

1. **Observed failure:** the earliest causal blocker in the newest relevant
   journey.
2. **Unreachable human affordance:** an intention exposed by a game-derived
   denominator but still classified missing, or a human surface for which no
   denominator adapter exists yet.

A run can only fail at something the agent could attempt. Therefore absence is
eligible even when no run mentions it. Before selecting a slice, name one
candidate from each queue and compare their effect on sustained play.

Choose one complete causal slice. Safety, evidence corruption, launcher truth,
or an unclassified game-enumerated affordance preempts ordinary feature work.
Otherwise choose the candidate that most increases useful autonomous play,
explaining why the other candidate waits.

Reconnaissance is valid work when it replaces a self-authored denominator with
a game-derived one and installs a failing classification gate. A derived,
reviewable queue can be the result of an invocation; do not force a speculative
gameplay implementation into the same slice.

## Derive the state packet

Do not trust this prompt or a prior summary for current facts.

1. Inspect branch, status, recent commits, and changed files. Preserve unrelated
   work and resolve concurrent ownership before editing.
2. Read `STATUS.md`, `ARCHITECTURE.md`, `SECURITY_AND_SAFETY.md`, the active
   profile, and relevant ADRs and guides.
3. Read the generated action catalog, game-binding parity report, modeled
   interface audit, and mutation attestation. Generated output is authoritative
   only for the source that actually generates it.
4. Run the portable baseline and generated-artifact checks. On a host with
   Kenshi installed, the parity tests must compare the captured denominator
   with the installed game.
5. Read the generated observed-blocker ledger for what recent runs actually
   failed on, then inspect the newest complete relevant `runs/<run-id>/`
   bundles behind the rows that matter. Use `kenshi-agent summarize` and
   `kenshi-agent aggregate-affordances`; do not infer a blocker from a final
   stop reason alone. The ledger groups failures into signatures and records
   which run last exhibited each; it does not attribute an owning boundary or
   decide anything is fixed, so both remain yours to establish. On a host
   holding run bundles, regenerate it with
   `python scripts/export_blocker_ledger.py` so new evidence enters the
   record — a gate fails when it is behind.
6. Record the observed-failure candidate, the unreachable-affordance candidate,
   their evidence, and the selected slice in the working commentary.

If the evidence cannot distinguish plausible causes, add the smallest missing
instrumentation and use it immediately. Do not stop at “better logging” when a
focused run can close the attribution.

## Affordance parity contract

Never use a project-authored action list as the denominator for human
affordances. Each surface adapter starts from the strongest enumerable
game-owned source available:

- named input bindings from Kenshi configuration;
- UI controls and ownership from bounded or on-demand widget-tree evidence;
- world/context operations from exact object kinds and their current actions;
- other surfaces only when their game-owned source and completeness boundary
  are explicit.

Do not collapse these into one pretend-complete percentage. Each adapter has
its own coverage boundary.

Every enumerated item has exactly one decision:

- **wired:** a planner-visible semantic route exists and the report can derive
  that claim from current code;
- **exempt:** a typed safety, debug-only, or supersession reason exists;
- **missing:** a grounded implementation-queue description exists.

Unclassified items, stale decisions, invalid routes, and expansion beyond the
reviewed missing ratchet fail the suite. A new Kenshi item must become visible
as a red gate, not disappear inside a denominator written by this project.

“Wired” proves reachability, not competence. A usable semantic affordance also
needs current binding evidence, safety ownership, bounded execution, and a
causal terminal. Complex mechanical chains may become controller-owned options,
but target, goal, quantity, risk, and strategy remain with the model.

Planner-authored affordance candidates are non-authoritative engineering
signals. They may accompany accepted useful output without spending a gameplay
step. Rejected, stale, unsafe, ambiguous, or failed existing routes are not
automatically missing capabilities. Promotion requires an engineer-owned route,
safety contract, and terminal proof.

## Failure dossier

For an observed failure, establish:

- run ID, profile, planner/provider/model, scenario or campaign, start state,
  objective, and requested budget;
- useful milestones actually reached;
- first world revision or step where useful progress ceased;
- earliest causal failure and its owning boundary;
- exact authored context, action/option, telemetry/UI evidence, receipt, and
  host event involved;
- whether the agent noticed, recovered, repeated, or changed goals;
- human intervention and why it became necessary;
- final game, UI, native-command, ownership, pause, and speed state;
- the class-level invariant that would have prevented or bounded the failure.

Classify before editing: launcher/config authority, planner transport,
planner contract/context, strategic continuity, observation/world model,
action contract, UI liveness, executor/concurrency, native/host stability,
continuity subsystem, or missing affordance.

A present action that cannot be selected, bound, completed, recovered, or
remembered is an existing-surface defect, not a missing affordance.

## Complete-slice method

1. Derive the state packet and run the baseline.
2. Compare one observed blocker with one unreachable human affordance.
3. State the selected boundary, invariant, scope, non-goals, and acceptance
   evidence.
4. Add a portable failing reproduction or adversarial invariant and observe it
   fail for the intended reason.
5. Fix the class at the owning boundary, making the defect impossible where
   practical.
6. Run focused tests and deliberately break or reverse the new behavior to
   prove the test can go red.
7. Use focused mutation testing for the changed behavioral boundary. A strict
   shard must regenerate trustworthy inputs and leave no survivor; diagnostic
   presentation may be excluded only narrowly with adjacent rationale.
8. Run full portable tests, Ruff, mypy, generated-output checks, diff checks,
   doctor/preflight, and relevant native build/conformance gates.
9. When live evidence is required and supervision is available, rerun the exact
   scenario past the former boundary before advancing one soak rung.
10. Preserve raw evidence, inspect final state, recover after abnormal exits,
    commit one logical slice, and leave the next blocker attributable.

For silent-loss defects, identify state ownership and assert conservation across
the pipeline. Instrument populated-to-default transitions and run the existing
soak. Do not patch individual fields when the ownership boundary is wrong.

For transient planner failures, bound retries and feed precise rejection back
to the next call. Never relax validation, dispatch stale authority, infer
success, or retry an at-most-once command whose delivery is uncertain.

Every supported UI state reached by an agent action needs either a bounded route
back to a usable world state or a typed terminal explaining why no safe route
exists. Repeated Escape presses are not a recovery design.

## Planner context discipline

The playing model needs current semantic affordances, relevant preconditions,
result vocabulary, exact authority, active plan state, recent outcomes, and
selected durable context. It does not need every runtime invariant repeated in
prose.

Measure system, projected schema, observation, images, response, and total
request size separately. Keep stable cacheable prefixes before dynamic state,
project the schema to the exact current action surface, validate fallbacks
against that surface, and expose cache read/write diagnostics. A cache claim
without provider diagnostics is unverified.

Prompt and schema budgets are hard ratchets. A new rule or action branch must
pay for itself instead of silently growing every planner call. Preserve exact
IDs, current-observation precedence, safety semantics, uncertain outcomes, and
active-plan patch rules while reducing duplicated explanation.

## Evidence and live work

Keep portable, replay/simulated, Windows, native build/load, supervised-live,
and repeated-live evidence distinct. Sent input, a staged DLL, or a planner
receipt is not proof that Kenshi accepted an action. Require a resulting frame,
fresh advancing telemetry, and the action’s causal terminal.

Use fixed starts and rerun the same scenario first. The default supervised soak
ladder is 30, 75, 150, then 300 steps; skip only rungs already proven by current
relevant evidence. A step ceiling is never a success criterion.

Use `./dev launch --preflight-only` before live work and supported `./dev`
commands rather than raw input snippets or direct request files. Give every run
a unique descriptive ID and never edit an old bundle.

While the operator is present, this method carries standing authorization for
ordinary gameplay on project-owned authored starts and explicitly designated
disposable test saves. It does not authorize non-project saves, arbitrary input
outside Kenshi and repository tooling, disabled safety gates, secrets or
external accounts, unrelated system settings, publishing, or destructive host
operations.

## Final report and stop

Report the failure or affordance dossier, violated invariant, completed slice,
portable and mutation evidence, Windows/native/live evidence, former boundary
and distance beyond it, safety/final-state review, untested claims, commits,
working-tree state, and next blocker. Label every evidence class and include run
IDs for live claims.

Stop after the selected class is fixed, applicable gates are green, required
regression evidence passes, the worktree is intentionally committed or
explained, and the next blocker is attributable.

Stop earlier only when continuing needs unavailable supervision, unapproved
authority, non-project save access, trustworthy telemetry that is absent,
resolution of unrelated edits, or an external platform change. Leave the
strongest safe increment and the exact unblock condition.
