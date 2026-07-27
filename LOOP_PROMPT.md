# Engineering loop — Kenshi Agent Environment

Copy this whole document into a capable coding agent whose working directory is
the repository root. Reuse it for successive invocations. Treat the current
checkout, `git log`, and `STATUS.md` as the source of truth — never this
document's description of the past.

One invocation delivers **one bounded slice**, fully finished and green.

## Where the project actually is

Verify this; do not assume it. It was accurate at protocol `1.1.0`.

The portable core is a deterministic Kenshi-like mock with strict Pydantic
models, JSONL lifecycle logs, exact entity-scoped SQLite memory, generated
schemas, and heuristic/scripted/subprocess/OpenAI/OpenRouter planners.
`single_step` is the default scheduler; feature-flagged `continuous` planning
accepts bounded typed plans, future patches, and exact opt-in active-movement
interruption behind deterministic acceptance.

Live work runs through one observation pump, a bounded `WorldStateStore`, an
independent safety supervisor, and a final in-lease authorization fence. The
planner-visible surface is a contracted action catalog
(`docs/generated/ACTION_CATALOG.md`), not a fixed recipe. A read-only strategic
advisor and a typed, cross-run aggregable `request_affordance` channel exist;
both emit zero game input and grant no authority.

The native plug-in exports telemetry at ~2 Hz and accepts seven reviewed
commands. Protocol `1.1.0` is live-loaded: nearest mining-resource observation,
the UI-facing current goal, exact contextual operation, retained production
through actual Copper output, and exact contextual-inventory opening are proven
on one resource. Conservation-proved collection still lacks live proof. Hidden
task scores grant no authority, and unresolved stale indoor handles fail closed.

Live evidence is thin by design and thinner than it looks: single supervised
runs, one host, one save, mostly one town. Read `STATUS.md` for the honest
limitation list before claiming anything generalizes.

The long-term [game-affordance platform
direction](docs/ADR_GAME_AFFORDANCE_PLATFORM.md) is operationalized below. It
does not widen the current Kenshi scope or justify speculative abstraction.
Generalization waits for evidence from a substantially different second game.

## Program trajectory

The mission is to make Kenshi the strongest evidence-grounded reference
implementation of an agent that can perceive a game faithfully, pursue
meaningful goals safely, discover when its action vocabulary is insufficient,
and turn those grounded gaps into proven semantic capabilities. Only after that
flywheel works across materially different Kenshi situations should a second
game test which substrate boundaries are genuinely reusable.

`request_affordance` is the demand signal for capability growth, not authority
to expose an internal method and not a substitute for the observation, safety,
execution, memory, and evaluation baseline beneath it:

```text
play a meaningful scenario
        ↓
encounter a grounded blocked intention
        ↓
retain and aggregate a typed affordance request
        ↓
select the highest-value recurring or survival-critical gap
        ↓
implement one vertical observation → action → causal-proof capability
        ↓
promote it into the planner-visible catalog only after evidence
        ↓
replay the scenarios and measure what is blocked next
```

Advance through evidence-gated phases, never by date, aspiration, an empty
hand-written queue, or declared UI coverage:

1. **Trustworthy Kenshi substrate.** Close known observation/authority
   conflations, unsafe exit ownership, weak causal terminals, identity gaps,
   and unmeasured tests. Preserve supervised live evidence and failure artifacts.
2. **Grounded capability flywheel.** Aggregate requests across diverse saves,
   promote vertical capabilities through the full contract, and show that replay
   converts prior blockers into causally proven actions without weakening safety.
3. **Demonstrated game-playing competence.** Complete meaningful survival and
   economy objectives across a deliberate scenario matrix. Measure outcomes,
   recovery, and recurring capability gaps rather than merely counting controls.
4. **Cross-game validation.** Apply the substrate to a substantially different
   second game. Extract a general kit only from seams that recur with evidence;
   leave game vocabulary and mechanics in adapters.

Within the current phase, keep one bounded slice per invocation but make it
compound. State which phase gate or flywheel edge the slice advances. A
capability slice is vertical: truthful observation and identity, typed planner
contract, current-state binding and input-boundary revalidation, bounded
execution, causal terminal evidence, generated schemas/catalogs, adversarial
tests, and the strongest safe runtime proof available. Horizontal
infrastructure is justified only by a concrete failing invariant or a measured
blocker to this flywheel.

Affordance aggregation must preserve both a stable game-neutral intent class
and a game-specific capability slug plus its grounded evidence and urgency.
Do not pretend one game's nouns are universal, and do not automatically promote
a frequent request: an engineer still owns observability, safety, semantics,
and evidence review.

## Non-negotiable engineering rules

### 1. Truthful control modes

`interface_only` is the default and must never present a native-assisted result
as ordinary UI-only play. Interface-only observations must not advertise
native-assisted actions, and interface-only guards and environments must reject
native commands even when a planner fabricates one. `native_assisted` requires
its own acknowledgement. Run headers, observations, receipts, events, overlays,
summaries, and benchmarks all carry the mode; never aggregate the two without a
visible breakdown. Do not delete a useful native feature to simplify messaging —
keep the implementation and the evidence honest.

### 2. Prefer the strongest stable evidence surface

Existing plug-ins, macros, and conventions are inputs, not a ceiling. Prefer
semantic current UI/entity anchors over fixed pixels, and one bounded native
observation over screenshot inference when the fact is safely available on the
existing UI-thread hook. Keep native observation separate from native action
authority: a read-only MyGUI bound does not authorize MyGUI callback invocation.
For stability work prefer durable launcher/configuration profiles, measurement,
and reversible settings. Do not add DirectX interception or broad native control
merely because C++ is available.

### 3. Safety is independent of the strategic model

Emergency stop, human input, stale or stalled telemetry, lost capabilities,
unexpected unpause, budget exhaustion, target loss, and dangerous screen
transitions are handled by deterministic code that never waits for an LLM. A
slow, blocked, or obsolete planner must not delay preemption. Repeated
cancellation is idempotent. Cleanup succeeds on causal evidence, not intent.
Any safety defect discovered while pursuing another queue item preempts that
item: first establish the safest observable state, then fix the whole exit or
authority class with a failing invariant before resuming the original proof.

### 4. Missing information stays unknown

A missing or invalid value must never silently become `0`, `false`, an empty
list, `world`, or `neutral`. Capabilities mechanically govern which fields may be
trusted; withdraw them during title/loading/save transitions, null-pointer
states, degraded sampling, protocol mismatch, or uncertainty. Condition
evaluation preserves `true`, `false`, `unknown`, `unavailable`, and `stale`, and
never collapses the last three into `false` for convenience.

### 5. Every state-changing action needs causal evidence

A snapshot predating the command never confirms it, even when it is within the
staleness threshold. Capture an action-start revision, use a unique command ID,
require an advancing revision or matching acknowledgement, and evaluate
postconditions only on causally later revisions. Timeout after possible delivery
is inconclusive unless the protocol proves rejection. Never automatically retry
an ambiguous or at-most-once action.

### 6. Revalidate at the actual input boundary

Validation before entering a polite input lease is necessary but not sufficient —
a delayed lease can make UI, target, capability, or calibration evidence
obsolete. Ordinary planner-authored actions in both schedulers carry an execution
token that runs after the lease is acquired and immediately before the first
primitive. Stale, unknown-age, or configured over-age telemetry and any changed
action, reference, or plan authority release the lease with zero input. Never
paper over this by shortening the lease timeout or disabling polite handoff.

### 7. Plans are bounded data, not programs

The model may emit strict typed conditions, bounded branches, known
actions/options, retries, timeouts, and budgets. It may not emit code, shell,
arbitrary expressions, raw controller calls, recursion, or unbounded loops. The
executor owns real-time plan state; a late response is advisory until its
revision, assumptions, version, protected step IDs, and remaining budgets are
rechecked.

### 8. Budgets are transactional and conservative

Reserve, commit, release at both plan-risk and global rate/purchase layers. A
proven command-matched non-dispatch releases both; accepted or ambiguous delivery
commits both. A purchase must never become executable twice because a timeout or
foreign receipt was mistaken for rejection.

### 9. Preserve the safe regression path

Keep `single_step` supported. Do not weaken F12, the dual live-action gates,
native acknowledgement, pointer envelopes, rate limits, purchase limits,
human-input yielding, capability checks, or re-pause semantics. Continuous mode
stays additive until its evidence is stronger.

### 10. Control ownership is explicit

Human input cancels current work and yields only after the deterministic safety
path verifies pause. `human_control` is visible in terminal, log, and overlay.
Quiet time may start a visible resettable `takeover_pending` countdown; quiet
time alone never returns authority. Any new input restarts it; F12 disarms it
terminally for the run. Countdown completion is advisory until freshness, loaded
and paused state, control mode, revision advance, active-command state,
calibration, and remaining authority are revalidated. Never resume the cancelled
plan — replan from the current revision.

### 11. No fabricated evidence

Code inspection is not a runtime test. A compiled DLL is not a loaded DLL. A
loaded DLL is not valid telemetry. A model-produced action is not an executed
action. An issued command is not a successful command. A current snapshot is not
post-command proof unless it is causally later.

Label every claim as one of: automated portable evidence; deterministic
live-shaped simulation; Windows integration evidence; native build/load
evidence; supervised live Kenshi evidence; historical evidence; proposed design.

### 12. The supported launcher is part of the capability

Live evidence must travel through `./dev launch` and `./dev journey`, including
deterministic subprocess planners. Terminal crashes travel through `./dev crash`,
which archives the current dump, logs, telemetry, settings, and frame before any
explicit dismissal. The wrapper owns WSL-to-Windows Python, path translation,
execution gates, logs, and final-state reporting. Direct Windows-Python commands,
hand-written native request files, ad-hoc SendInput snippets, and PTY launcher
attempts are diagnostics only and never accepted planner capability evidence. If
the supported path cannot express or safely finish a test, fix it in the current
slice; do not normalize a workaround into the procedure.

## Standing supervised live-test authorization

Standing authorization for **bounded, non-destructive Kenshi interaction while
the operator is present**. Do not ask before every harmless click or pulse.

Authorized once the ordinary execution gates pass: focus Kenshi; pause or resume
within bounded option semantics; select the known test character; issue bounded
movement or dialogue approach; open dialogue; activate an exact visible
non-destructive control; abort, pause, or hand back; restart the agent process
after a software failure.

Print one concise line stating the intended chain and its expected stop
condition before starting. Keep F12 and human-input cancellation active.

Still requires explicit current authorization: spending or selling valuable
resources unless the save is explicitly disposable; theft, combat initiation,
dismissal, recruitment payment, or irreversible inventory change; deleting or
overwriting saves; changing system settings outside the established test setup;
publishing, pushing, opening pull requests, or acting in external accounts; any
input outside Kenshi and this project's own tooling.

A supervised live test is evidence, not a substitute for portable tests. Equally,
do not postpone a harmless supervised proof because some theoretical edge case
is unmodelled.

## Startup procedure

1. **Read what is current.** `STATUS.md` for capability and limits;
   `docs/generated/` for the action surface; `docs/ADR_*.md` for why a boundary
   exists; `docs/GUIDE_*.md` for procedures; `docs/REPORT_*.md` for analysis
   across runs; `git log` and `runs/<run-id>/` for what happened. There is no
   prose ledger, and you must not create one.
2. **Also inspect** `README.md`, `ARCHITECTURE.md`, `SECURITY_AND_SAFETY.md`,
   `pyproject.toml`, `config/default.yaml`, the active live profile, and
   `prompts/planner_system.md` when relevant.
3. **Run the baseline before editing**: `uv run pytest -q`, `uv run ruff check .`,
   `uv run mypy src`. A red baseline is the slice.
4. Before live work, run `./dev launch --preflight-only`; launch and journey
   commands use ordinary captured pipes, never a pseudo-terminal.

## Current priority queue

Work top-down. Verify each is still open before starting it.

1. **Live-prove the resource-production option.** Portable and native-build
   evidence now retain the exact job through actual output, adopt unchanged
   matching work without reissue, open that target's contextual inventory, and
   require equal source loss/destination gain for collection. A matching later
   destination quantity lacked simultaneous source-loss and planner-causality
   proof. Prove the whole autonomous chain through the supported launcher; no
   collection or income claim passes without causal inventory or money evidence.
2. **Execute the fixture-attested scenario matrix.** Use the bundled starts to
   create source saves; capture dynamic states; use `./dev scenario`, `launch
   --scenario`, and `journey --scenario` across indoor/outdoor, hostile/safe,
   broke/funded, solo/squad, and day/night. Manual labels and raw reruns are not
   recurrence evidence. Select the first gap supported across verified situations.
3. **Expand controller-verified contracts.** Most success conditions are still
   planner-authored, so later correlated state can pass as the intended effect.
   Each contract moved to a controller-owned typed terminal verdict removes one
   class of false success.
4. **Add mutation testing.** Test count is not coverage; `mutmut` or
   `cosmic-ray` over `src/kenshi_agent/` is the only cheap answer to whether the
   suite would notice broken code.
5. **Remote map travel.** No semantic action exists at all; `move_to_character`
   is bounded to the nearby-character query.
6. **Native identity validation** across recruit, dismiss, reorder, KO, death,
   save/load, and zone transitions.
7. **Harden hosted structured-output truncation.** The advisor can return an
   EOF-truncated JSON string within its wall-time allowance; make that failure
   attributable and recoverable without inventing a brief.

## Continuous-planning semantics

The decisions are recorded in
[`docs/ADR_CONTINUOUS_PLANNING.md`](docs/ADR_CONTINUOUS_PLANNING.md) and the
companion ADRs it links. Read them rather than re-deriving. The shape to
preserve:

```text
continuous telemetry and event ingest
        ↓
versioned world-state store
        ↓
independent deterministic safety
        ↓
interruptible option/plan executor ← strict bounded plan or typed patch
        ↑
asynchronous strategic planner
```

Rules 5 through 8 govern the edges of that diagram; the ADRs govern the rest.
A deterministic reflex layer stays beneath all of it.

## Testing requirements

**A test must be able to fail.** Break the source, watch your new test go red,
restore it. A test never seen failing is unmeasured.

- **Never assert on source text.** `assert 'PROTOCOL_VERSION = "0.8.0"' in source`
  is a freshness check wearing a test costume: it breaks on every deliberate
  bump and verifies no behavior. Generate the artifact and diff it.
- **Prefer invariants over enumeration** for pure functions. Binders are
  `(action, observation) -> binding`; generate observations and assert what must
  never happen (never binds an absent target, never binds when stale, ambiguity
  always fails closed).
- **Update every place a change touches.** A half-updated registry assertion is
  worse than none, because it reads as coverage.
- **Cover the failure path.** Every behavior change needs at least one failure
  path and one safety/cancellation/unknown-state path.
- **Reviewing tests is adversarial, not additive.** Find an input where the code
  is wrong and the suite stays green, then deliver that failing test.

**Fix the class, not the instance.** When a bug surfaces, name its class and add
the invariant covering the class. A defect recurring in a new guise means a
boundary is underdefined. For silent-loss classes — state one component wrote
being destroyed by another's later write — make ownership explicit and fail
closed on foreign writes, instrument for populated→default transitions, and
assert conservation across the whole pipeline. Prefer making a bug impossible
over making it detectable.

## Documentation discipline

`tests/test_docs_hygiene.py` and `.githooks/pre-commit` enforce this
mechanically; do not argue with them. This file carries its own recorded
ceiling and may only ratchet downward.

- Every document is ≤120 lines and is one of: `docs/ADR_*.md` (a durable
  decision, written once, superseded not edited), `docs/GUIDE_*.md` (a procedure
  or wire contract restating no code), `docs/REPORT_<YYYYMMDD>_<topic>.md` (a
  dated analysis record, written once — supersede it by writing a later report,
  never by editing an earlier one), or `docs/generated/*` (emitted from code).
- Anything restating code must be generated and staleness-gated.
- Anything restating history belongs in `git log` and `runs/<run-id>/`. A report
  is for reasoning *across* runs — comparison, attribution, the argument behind
  a design change — which no single commit body can hold.
- **Do not create a running engineering narrative.** One was deleted at 2,300
  lines after drifting inside a week.
- Commit subjects stay short. A commit body is warranted when the commit lands
  evidence with no home in code — a run ID and what it proved or failed.
  Rationale goes in code comments.

## Per-invocation method

1. Establish state — `git status`, `git log`, branch, working-tree cleanliness —
   and confirm the live authorization boundary. Never edit a tree another agent
   is mid-way through.
2. Choose one slice; state problem, scope, non-goals, acceptance criteria.
3. Write failing tests first, including a failure path and a safety path.
4. Implement the smallest complete design. Reuse current boundaries; never build
   a parallel unintegrated framework.
5. Run focused tests continuously so failures stay attributable.
6. Run the full gates: `pytest`, `ruff`, `mypy src`, schema and generated-doc
   export comparison, doctor, fixed seeds, relevant continuous proofs.
7. Inspect the diff for secrets, run artifacts, temporary payloads, binaries,
   and unrelated churn.
8. Update schemas, config, prompts, and generated docs. Distinguish implemented
   from proposed behavior.
9. Report honestly. No Windows, native, or live claim without the matching run.
10. Stop when the slice is complete. Do not start the next one.

When a slice reveals a deeper flaw, do not hide it behind compatibility code.
A safety or supported-launcher defect joins the current slice and preempts its
original acceptance run. Otherwise solve the flaw inside the declared scope or
record a precise next item naming the failing invariant.

## Required final report

```markdown
# Engineering Loop Result

## Slice completed
## Why this was the right next slice
## Changes
## Evidence
(label each: portable / simulated / Windows / native build-load / supervised live)
## Safety and experiment-boundary review
## Not tested
## Working-tree state
## Next slice, with its failing invariant
```

## Stop conditions

Stop and leave a precise report rather than widening scope when: the slice is
complete and green; a live step needs authorization not present in the current
request; a focus-taking or input-injecting test lacks a current operator
statement that the computer is clear; an unresolved product or
experiment-boundary decision materially changes policy; a platform or dependency
issue blocks the next safe step; native/telemetry state is untrustworthy;
unrelated pre-existing changes make target files unsafe to edit; or continuing
would require unapproved control of Kenshi or another sensitive context.

**Do not stop merely because the problem is difficult.** Produce the strongest
complete local increment available and make the next step exact.

Begin by establishing repository state, running the baseline, and selecting
exactly one highest-priority bounded slice.
