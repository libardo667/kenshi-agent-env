# ADR: action completion authority

Status: accepted (2026-07-28)

Supersedes the completion-ownership portions of
`ADR_CONTINUOUS_PLANNING.md`; its scheduler and plan-authority decisions remain
in force.

## Decision

Choosing an action and proving its mechanical effect are separate authorities.
The planner chooses the intention. Completion resolves through exactly one of
three paths:

- a controller-owned typed terminal;
- runtime-owned conditions derived from the action and the immediate
  pre-dispatch observation; or
- planner-authored conditions only when the effect is genuinely ambiguous.

Runtime conditions are derived once at the step's dispatch boundary, never when
the whole plan is accepted. The planner need not repeat them, but may add a
distinct strategic condition such as the native-command handoff required after
an interrupted movement. If the runtime's required baseline is unavailable,
dispatch fails closed.

## Consequences

The model does not restate that a purchase lowers current money, a sale raises
it, a named window close lowers the open-window count, a reversible binding
changes its current state, or a playback action establishes its named state.
Sequential actions compare against their own fresh baselines. Adding an action
does not require editing a schema-level exemption list.

An ambiguous UI control, camera gesture, equip, or scroll still needs an
observable planner-authored outcome until its contract gains stronger runtime
or controller evidence.
