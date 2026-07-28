# ADR: resource harvesting is one semantic option

Status: accepted, 2026-07-28

## Context

Mining originally exposed production, source-inventory opening, and output-cell
transfer as three planner actions. That made the model reconfirm the mechanical
consequences of its own decision, left controller-owned windows open between
plans, and allowed planner latency to split one intention across unrelated
world revisions.

## Decision

The planner authors one exact `harvest_resource(actor_id, target_id, quantity)`
action, with quantity bounded from one through five.

The controller privately selects gear 3 and confirms Kenshi's observed 5x
multiplier, retains the exact native production job to the requested yield,
restores gear 1 before any inventory input, opens the exact resource and actor
inventories, derives one unambiguous bounded output stack from current
telemetry, and repeats the one-item transfer gesture no more than the requested
quantity. Every iteration rebinds the current stack and proves equal positive
source loss and actor-inventory gain before the next gesture; the outer receipt
aggregates those proofs and succeeds only at the exact requested yield. The
controller then closes only those two owned windows. Each phase rebinds current
identity and input authority. Speed restoration remains authorized even when
production failed or the actor became unsafe. Safety supervision and a
validated strategic interruption may still stop the monitored production
phase.

`produce_resource_output`, `open_context_inventory`, and
`collect_resource_output` remain typed internal actions for phase authority and
evidence, but are not planner-visible. The outer action succeeds only with a
typed `harvested` verdict and confirmed cleanup.

## Consequences

- One strategic mining decision no longer becomes several model decisions.
- Existing matching work may be adopted only when its acknowledged minimum
  yield matches the new request.
- Slow production does not consume model turns, and a failed or interrupted
  production phase cannot intentionally leave the world running at 5x.
- Observation-pump state owns current truth; an older phase receipt is retained
  as evidence but cannot be republished over a later world revision.
- A transfer gesture is not success; each collected item needs its own causal
  conservation proof, and partial collection remains a failed outer action.
- An inventory or cleanup ambiguity fails closed without inventing another
  click.
- Native request schema 1.1 and telemetry protocol 1.2 add the bounded minimum
  yield to request and acknowledgement identity.
