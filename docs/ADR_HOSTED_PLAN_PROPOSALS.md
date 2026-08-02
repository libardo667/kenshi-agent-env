# ADR: Hosted models propose intent; the runtime compiles plans

Status: superseded in part by
[concurrent plan proposals](ADR_CONCURRENT_PLAN_PROPOSALS.md)

## Context

`PlanEnvelope` mixes strategic choices with facts already owned by the runtime:
revision fences, graph IDs, branches, retries, idempotency, timeouts, and risk
budgets. Requiring a language model to reproduce all of those facts exactly
makes harmless serialization noise indistinguishable from an unsafe plan and
turns recoverable proposal errors into run-level failures.

## Decision

An idle hosted continuous planner returns `PlanProposal`: one objective, an
ordered list of semantic actions, optional outcomes only for planner-owned
ambiguous effects, and optional continuity or fieldbook proposals using plain
evidence IDs.

Deterministic code compiles that proposal against the immutable prepared
observation. It owns the plan identity and version, world revision and control
mode, freshness fences, step graph, completion ownership, retries,
idempotency, time and action ceilings, and cumulative risk and spend budgets.
Model-authored values for those fields are discarded rather than trusted or
validated.

Continuity and fieldbook items compile independently. An invalid item is
quarantined with its surface, index, and cause while valid gameplay and valid
siblings remain usable. The exact legacy `plan_outcome` reference shape that
selected this boundary is normalized during migration; a second general
wrapper language is not supported.

The active OpenRouter adapter turns malformed idle proposal syntax or an
unavailable proposed action into a runtime-authored `noop` that requests a
fresh planning turn. It records the rejected cause and emits no game input.
Transport failures, empty or truncated responses, unsafe action authority, and
input-boundary rejection remain hard, attributable failures.

`PlanPatch` retains its strict optimistic-concurrency contract. Compiling
fallible change-course intent is a separate boundary.

## Consequences

- Language-model formatting is no longer an authority boundary for a new idle
  plan.
- The model cannot enlarge retry, time, action, purchase, pointer, or native
  authority by naming different envelope values.
- Safe recovery preserves continuous play without inventing a game action.
- Proposal compilation is independently mutation-attested because it is the
  owner of the derived safety envelope.
