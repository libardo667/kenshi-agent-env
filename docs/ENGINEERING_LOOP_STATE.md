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
- Continuous planning is proposed, not implemented.

## Completed milestones

- Deterministic mock environment and one-day survival baseline.
- Strict telemetry, observation, action, decision, receipt, and memory models.
- Guarded Windows input, bounded movement pulses, and outcome feedback.
- Narrow supervised Hub Barman approach/trade/one-item purchase evidence.
- Typed, mechanically enforced control modes carried through observations,
  receipts, run lifecycle events, overlays, CLI/log summaries, schemas,
  benchmarks, and current documentation.

## Latest completed slice: explicit control modes

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

## Current checks

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

- Automated portable evidence for the completed slice:
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
- Nearby and squad ordinal IDs are unstable.
- Observation payload truncation can produce malformed JSON.
- Several declared config fields remain behaviorally unused.
- There is no CI workflow or Python lockfile.

## Ordered next candidates

1. P1: strict bounded `PlanEnvelope`, typed capability-aware conditions, graph
   validation, feature-flagged `single_step`/`continuous` scheduling, and a
   deterministic two-action mock proof from one planner call.
2. P2: monotonic world-state revisions, causal waits, event journal, and
   independent observation pump.
3. P3: independent safety supervisor that preempts a blocked planner.
