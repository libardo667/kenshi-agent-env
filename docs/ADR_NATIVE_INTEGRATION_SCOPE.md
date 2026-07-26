# ADR: Right-sized native integration

## Status

Accepted on 2026-07-26 after the project owner clarified that the native plugin
is not intended to be "small", "medium", or "large". Its intended size is the
size required for an agent to play Kenshi faithfully across the game's
surfaces.

## Decision

Treat the native plugin as an authoritative, capability-gated Kenshi
integration layer whose scope may grow with the playable surface.

The planner should express game intent in semantic terms. The controller and
plugin should own deterministic mechanics the model should not have to
finagle: stable object identity, current UI/game facts, door or destination
resolution, native player orders, bounded recovery transactions, and keyed
terminal evidence.

Plugin size is not a design constraint. The constraints are:

- truthful capabilities and schemas at every boundary;
- strict command identity, current-state revalidation, and bounded effects;
- no silent blending of `interface_only` and `native_assisted` evidence;
- maintainable reviewed abstractions rather than an arbitrary native method
  dispatcher;
- portable, native-build, and live proof labelled separately;
- no direct money, health, faction, position, save/load, editor, or other
  cheat-like mutation unless a later explicit product decision changes that
  boundary.

## Consequences

- A multi-stage or game-specific native subsystem is appropriate when it is the
  honest way to expose a Kenshi surface reliably.
- UI input remains useful where it faithfully exercises the game, but semantic
  actions need not be forced through brittle pixels to keep the DLL small.
- New capabilities should be added vertically across native telemetry or
  control, Python models, contracts, execution, schemas, tests, and durable
  evidence.
- The current bridge remains narrow by reviewed capability, not by line count.
  Faithful coverage may eventually require substantially more native code.
