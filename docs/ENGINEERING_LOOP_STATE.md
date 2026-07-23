# Engineering loop state

## Current contract

- The portable `single_step` runtime is the regression baseline. It asks one
  planner for one action, passes that action through `ActionGuard`, executes it
  through the selected environment, and records the observed outcome.
- Every run declares `interface_only` or `native_assisted`.
  `interface_only` is the default and cannot advertise or execute a marked
  native-assisted skill. Native-assisted live execution requires its own config
  opt-in and CLI acknowledgement in addition to the normal live-action gates.
- Live keyboard and mouse input requires both
  `safety.live_actions_enabled: true` and `--execute-live-actions`.
- F12 checks, stale-telemetry gates, pointer envelopes, action/rate/purchase
  limits, polite human-input yielding, and movement re-pause are invariants.
- Missing telemetry remains unknown and capability-gated.
- `single_step` remains the default. Feature-flagged `continuous` accepts a
  strict bounded `PlanEnvelope` in mock/fake environments only; live-labeled
  environments terminate before a strategic call or action.

## Completed milestones

- Deterministic mock environment and one-day survival baseline.
- Strict telemetry, observation, action, decision, receipt, and memory models.
- Guarded Windows input, bounded movement pulses, and outcome feedback.
- Narrow supervised Hub Barman approach/trade/one-item purchase evidence.
- Typed, mechanically enforced control modes carried through observations,
  receipts, run lifecycle events, overlays, CLI/log summaries, schemas,
  benchmarks, and current documentation.
- Typed bounded plan graphs, five-valued capability-aware conditions,
  executor-owned causal verification and budgets, lifecycle replay, and a
  deterministic two-action continuous mock proof.

## Previous completed slice: explicit control modes

Problem: the repository claims UI-only/read-only control while the native plugin
also exposes a hotkey that issues `PLAYER_TALK_TO` through a Kenshi internal
player-order method. That makes safety claims and future continuous-plan
evidence ambiguous.

Scope:

- Add typed `interface_only` and `native_assisted` modes, defaulting to
  `interface_only`.
- Mark native-assisted skills in configuration and schemas.
- Omit and reject those skills in `interface_only`, with both policy and
  environment enforcement.
- Require a dedicated configuration opt-in and CLI acknowledgement before
  native-assisted live execution.
- Carry the mode through observations, receipts, run events, overlays,
  summaries, metrics, and current documentation.

Non-goals:

- No new action capability.
- No continuous executor or telemetry transport changes.
- No Windows or live Kenshi execution.

Acceptance criteria:

- Automated tests prove interface-only omission/rejection and native-assisted
  opt-in/acknowledgement.
- Every new run artifact states its control mode.
- README, status, architecture, safety guidance, schemas, and live profiles
  agree with the implementation.
- The full portable baseline, static checks, doctor, schema comparison, and
  deterministic mock seeds pass.

Result: complete in the current worktree. No new action surface was added.
Interface-only filtering and rejection are enforced independently by the live
observation/environment boundary and `ActionGuard`.

## Latest completed slice: P1 typed bounded plans

Problem: `PlannerDecision` carried exactly one action and `AgentRuntime` waited
for a complete planner/action/observation cycle before every next action.
Continuity was therefore structurally impossible even when the prompt described
a longer intention.

Scope:

- Preserve `single_step` unchanged as the default and add feature-flagged
  `continuous` planning.
- Add strict, schema-exported world revisions, capability-aware typed
  conditions, bounded acyclic plan graphs, explicit control-mode binding,
  retries/idempotency, branches, action/wall/game/risk budgets, plan versions,
  and patch concurrency data.
- Add deterministic condition and policy evaluators that distinguish `false`,
  `unknown`, `unavailable`, and `stale`.
- Execute multiple plan steps through the existing guard/environment path
  without another strategic planner call.
- Re-evaluate plan assumptions and step preconditions immediately before every
  action; cancel a stale future step before guard validation or execution.
- Evaluate success/failure only on a causally later world revision.
- Emit replayable plan lifecycle events and add evaluator metrics for strategic
  calls, plan/step outcomes, actions per call, and stale/rejected work.
- Prove the first vertical slice in mock/fake event-driven environments only.

Non-goals:

- No independent observation-pump task or world-state event store (P2).
- No planner-concurrent safety supervisor (P3).
- No stateful live movement option or overlapping strategic planning (P4).
- No native command ID/stable-identity change and no live food chain.

Acceptance criteria:

- One planner response executes at least two guarded actions and logs one
  strategic call.
- A changed assumption or precondition cancels the next action before the
  environment sees it.
- A desired state on an old-but-wall-clock-fresh revision cannot satisfy a
  postcondition.
- Missing capabilities and null values remain `unavailable`/`unknown`, never
  false.
- Invalid branch targets, cycles, unreachable steps, unsafe retries, excessive
  horizon, and policy-exceeding budgets are rejected.
- Plan lifecycle replay reaches the same terminal state and metrics report
  calls, actions, completed steps, rejections, aborts, and causal failures.
- Existing single-step tests and fixed-seed outcomes remain green.
- Schemas, configs, planner adapters, prompt, docs, and ledger agree.

Result: complete in the current worktree. The default path remains
`single_step`. Continuous mode now accepts a versioned, bounded plan, executes
multiple actions through the ordinary guard/environment path, and refuses stale
plans or future steps before execution. It distinguishes condition uncertainty,
requires later revisions for postconditions, emits replayable lifecycle and
transactional budget events, and is deliberately blocked in live-labeled
environments pending later milestones.

## Current checks

P1 completion verification on 2026-07-23:

- `.venv/bin/python -m pytest -o addopts='' -q`: 111 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src/kenshi_agent`: passed, 42 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
  reported `control_mode interface_only` and `planning_mode single_step`.
- Fresh exported schemas matched checked-in `schemas/` byte-for-byte, including
  `plan.schema.json` and `plan_patch.schema.json`.
- Continuous run `p1-continuous-proof` completed `resume` and `accelerate` from
  one strategic call. Summary metrics reported two executed actions, two
  committed reservations, one completed plan, and
  `actions_per_strategic_planner_call: 2.0`; lifecycle replay reconstructed the
  same completed plan and step order.
- Single-step seeds 7, 11, and 19 retained the prior one-day outcomes in 25, 13,
  and 13 actions.

Baseline at `ebfe9248f2adabe1cb6ebf264ecb9ad67fec3c68` on 2026-07-23:

- `.venv/bin/python -m pytest -q`: 85 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src`: passed, 40 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed.
- Exported schemas matched `schemas/` byte-for-byte.
- Mock seeds 7, 11, and 19 each survived one in-game day.
- `.venv/bin/python -m pip install -e ".[dev]"` was unavailable because this
  virtual environment has no `pip` module; the equivalent
  `uv pip install --python .venv/bin/python -e ".[dev]"` succeeded.

## Evidence

- Automated portable evidence for the previous control-mode slice:
  - `.venv/bin/python -m pytest -q`: 91 passed.
  - `.venv/bin/ruff check .`: passed.
  - `.venv/bin/mypy src`: passed, 40 source files.
  - `.venv/bin/python -m compileall -q src scripts`: passed.
  - `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
    reported `control_mode interface_only`.
  - Fresh exported schemas matched checked-in `schemas/` byte-for-byte.
  - Mock seeds 7, 11, and 19 survived one day in 25, 13, and 13 actions.
  - Run `20260723T145942.137422Z` and its `kenshi-agent summarize` output both
    reported `interface_only`; all 25 receipts carried the same mode.
- Existing Windows and supervised live evidence predates explicit control-mode
  labeling and must not be merged into either mode's future metrics.
- Windows PowerShell launchers, Windows input, native build/load, and live
  Kenshi behavior were not tested in this slice.

## Known risks and deferred debt

- Native command acknowledgement lacks causal command IDs and revision fences.
- Telemetry remains latest-snapshot polling rather than an event-driven store.
- Strategic planning does not yet overlap execution; active plan patches are
  parsed/versioned but not applied.
- Continuous live execution and stateful cancellable movement options are
  intentionally blocked.
- Nearby and squad ordinal IDs are unstable.
- Observation payload truncation can produce malformed JSON.
- Several declared config fields remain behaviorally unused.
- There is no CI workflow or Python lockfile.

## Ordered next candidates

1. P2: monotonic world-state revisions, causal waits, event journal, and
   independent observation pump.
2. P3: independent safety supervisor that preempts a blocked planner.
3. P4: cancellable stateful options, planner/executor overlap, and optimistic
   future-step patch application.
