# ADR: Kenshi as a reference implementation for a game-affordance platform

Status: accepted 2026-07-26 as a long-term direction, not current scope.

## Context

Kenshi Agent Environment targets one game. Its architecture suggests a reusable
division between an agent substrate and a game-specific semantic adapter, but one
game cannot establish that an abstraction is universal.

The valuable problem is not merely discovering callable functions. A playing
agent needs current player-faithful observations, stable references, bounded
actions, and causal evidence of their effects. Game code, reflection, mod APIs,
runtime traces, UI structure, ordinary input, and vision can reveal candidates,
but none alone proves an affordance safe or semantically reliable.

## Decision

Treat Kenshi as a reference implementation for a possible game-affordance
platform:

- A reusable substrate owns planning, memory, safety, capability management,
  causal execution, logging, and evaluation.
- Game-specific adapters translate available integration surfaces into typed
  semantic observations and actions.
- Static inspection and runtime discovery propose candidate affordances.
  Controlled runtime evidence is required before a candidate becomes
  planner-visible.
- The affordance grammar is reusable; exact game vocabulary and mechanics remain
  game-specific.
- Information access and control modes remain explicit, so privileged integration
  is never presented as ordinary interface play.
- A substantially different second game is the decisive test before extracting a
  generic framework from Kenshi.

## Non-goals

- Zero-shot understanding of arbitrary games or binaries.
- Automatic invocation of discovered internal methods.
- Exposing hidden or cheat-like state as ordinary player knowledge.
- Weakening safety or causal proof to increase apparent coverage.
- Restructuring current Kenshi work around speculative reuse.

## Consequences

Near-term work continues making Kenshi reliable and broad enough to expose real
recurring seams. Clean adapter and substrate boundaries are preferred where they
cost little, but no abstraction is universal merely because Kenshi uses it.

Concrete progress belongs in code, generated contracts, executable benchmarks,
run evidence, and commits. Grounded affordance requests may eventually drive
adapter discovery, but engineers still own promotion, safety, and terminal
semantics. This record states direction; it is not a roadmap or claim of current
cross-game support.
