# ADR: One runtime-generated affordance contract

**Status:** Accepted (2026-08-02)

## Context

Planner-visible action unions, observation digests, prompt branches, macros, and
source-specific validators exposed overlapping ways to express the same
gameplay intent. The model also authored mechanics that deterministic code
could derive more accurately.

## Decision

The playing model selects only a current `AffordanceOffer` ID, its exact offered
target, and declared gameplay parameters. Runtime adapters enumerate all
supported sources, rebind a selection against the same current denominator, and
only then materialize a private typed operation.

Runtime code owns applicability, capabilities, preconditions, risk,
idempotency, selection mechanics, playback, monitoring, completion, retries,
and cleanup. Every selected affordance uses one lifecycle and terminal receipt
vocabulary. Adapter-specific evidence may refine that receipt but may not create
a second planner contract.

This is a clean break. There is no compatibility action union or legacy macro
translation. Context orders are runtime semantic identifiers rather than a
Python enum; an adapter may gain a new native route without adding a permanent
planner action class for that order.

Generated planner catalogs and parity reports derive from the adapter registry.
Private operation completeness remains a separate runtime gate. Each source
keeps an explicit completeness boundary; absence from one source is never
presented as global gameplay completeness.

## Consequences

- Hosted single-step and continuous schemas remain stable as capabilities grow.
- Exact current offer IDs fail closed across stale, absent, or ambiguous state.
- Planner prompts describe one contract instead of repeating action families.
- New source coverage requires adapter and denominator invariants, not sibling
  prompt branches or enumerated action cases.
