# ADR: Revalidate plan authority at the real input boundary

Status: accepted for portable continuous mode; not yet exercised in live Kenshi

## Context

The continuous executor re-reads the canonical revision and re-evaluates plan
assumptions and typed step preconditions immediately before
`environment.dispatch()`. That is the correct place for *policy* validation, but
it is not the last moment before input reaches Kenshi.

`LiveEnvironment` then acquires a polite input lease. The lease deliberately
waits for a quiet human-input turn, and that wait is unbounded by design: it is
what keeps the agent from fighting the operator for the keyboard. Anything the
executor proved before the wait can therefore be obsolete when the wait ends.
The selected character can change, a modal can open, the game can unpause, a
capability can be withdrawn, or the human can take control outright.

Two narrow fences already lived inside the lease: semantic developer-startup
clicks re-read their exact unique label and bounds, and every pointer-bearing
action rechecks the configured client width and height. Both are proven and
worth keeping, but neither carries the *step's* typed authority. Ordinary
gameplay dispatch had no such check.

Shortening the lease timeout or disabling polite handoff would close the window
by making the agent ruder. That is the wrong trade.

## Decision

- Introduce `ExecutionToken`, a bounded authorization object built by the
  continuous executor at the same moment it validates a step. It carries the
  plan/step/command identity, the control mode, the validated revision, the
  plan's assumptions, the step's preconditions, and a *deferred* accessor to the
  canonical world-state store.
- Pass the token through `AgentEnvironment.dispatch()` alongside the existing
  `CommandDispatchContext`. Environments with no real input lease accept it and
  do not re-check it, because they have no window to protect.
- `LiveEnvironment` runs the fence inside the acquired lease, after calibration
  and foreground prerequisites, and immediately before the first primitive.
- The fence re-reads the store's latest canonical observation and rejects when:
  no canonical observation exists, the revision regressed, the control mode
  changed, the observation carries `human_input_detected` or
  `emergency_stop_detected`, or any assumption or precondition is no longer
  `true`.
- Reuse `evaluate_conditions` rather than a parallel boolean path, so the
  boundary honours the same five-valued capability-aware semantics. `unknown`,
  `unavailable`, and `stale` block input exactly as `false` does.
- On rejection emit **zero** primitives and return a receipt with
  `accepted=false`, `executed=false`, `primitive_actions=0`, and
  `error_type="InputBoundaryRejected"`.
- Attach an `InputBoundaryReport` to the receipt in every token-bearing
  dispatch, recording the decision, reason, lease wait, plan/step identity, the
  validated and boundary revisions, and the bounded evaluations.
- Emit `input_boundary_revalidated` or `input_boundary_rejected` from the
  executor and count both in evaluator metrics.
- A proven non-dispatch releases its reservation through the existing
  `accepted=false`/`executed=false` path. Ambiguity after partial dispatch is
  untouched and remains conservatively committed.

## Consequences

A delayed lease can no longer convert stale authority into real input. The
rejection is attributable: the report names which condition failed, on which
revision, after how long a wait, so a live run can distinguish "the agent was
blocked" from "the agent was wrong".

The native-assisted path keeps its stronger issue-time DLL fences unchanged.
The boundary runs before them and is purely additive; command IDs, request
atomicity, and keyed acknowledgement semantics are untouched.

The calibration recheck deliberately still runs *first* and still raises, so the
existing fail-closed client-size brake is unchanged and is not silently demoted
into a boundary rejection.

This does not deliver P4. The fence re-checks the client-size gate that exists
today; a versioned calibration identity covering UI scale, DPI transform, window
mode, keymap, and profile hash is still missing, and the token is the intended
place to carry it when P4 lands.

Single-step dispatch does not build a token, because it has no plan assumptions
or typed step preconditions to re-check. Its receipts carry no boundary report,
which is why the report is nullable rather than defaulted.

This is automated portable evidence only. No live Kenshi run has exercised the
fence against a real input lease.
