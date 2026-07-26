# ADR: runtime affordance requests

Status: accepted, 2026-07-26

## Context

The playing model can only author controls already present in the planner
schema and current semantic-action catalog. During a live run it may discover
that a grounded goal cannot be expressed—for example, working an exact ore
resource through Kenshi's contextual world interaction. Previously it could
only improvise with unrelated controls, repeat itself, or stop. None of those
outcomes tells the engineering loop which control is actually missing.

The project owner wants capabilities to be requested as the agent plays, then
implemented and verified through the same evidence boundary as every other
control.

## Decision

`request_affordance` is a planner-layer cognitive action. It records:

- the desired capability;
- the current goal it blocks;
- why the capability is needed;
- exact current evidence;
- any safe available workaround; and
- survival-critical, goal-blocking, or fidelity-improving urgency.

It must be a plan's only step. It creates no world command and emits zero
keyboard, mouse, or native primitives. Its typed receipt says `retained` or
`duplicate`; both are terminal. Retained requests are included in subsequent
observations for the run, including across advancing telemetry publications.
Recording a request never grants the capability.

The planner should request only an immediate grounded gap after checking the
advertised semantic actions, visible controls, bindings, dialogue targets, and
travel destinations. It must not repeatedly request an already retained
capability. After a non-critical request, it should use a safe available
workaround or pursue another useful goal.

## Consequences

- Live runs can produce a structured, measurable implementation queue.
- The overlay and JSONL receipt explain the actual blocked intention rather
  than only the failed substitute action.
- Deduplication prevents one gap from consuming the run.
- An engineer still decides the authoritative binding, safety checks,
  completion semantics, and required native evidence before adding the tool.
- Planner context is runtime-owned. The world-state store carries advisor
  briefs, memories, action outcomes, and affordance requests forward when raw
  telemetry advances, rather than allowing the observation pump to erase them.

The first clarified target is a semantic exact-target contextual task, with ore
mining as the immediate survival/economy use case. It is not synonymous with
combat and will not be implemented as an ungrounded screen-coordinate
right-click.
