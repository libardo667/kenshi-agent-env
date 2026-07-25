# Documentation truth policy

Current as of 2026-07-25. This is the working standard for documenting changes
while the action surface, native bridge, and live evidence are evolving quickly.

The external review
`kenshi_agent_env_deep_systems_review_2026-07-25.md` is an audit input for this
policy. It reviewed a ZIP snapshot rather than the Git checkout, so each finding
must be rechecked against current code before it is repeated as current fact.
Its central requirement remains authoritative: preserve one truthful contract
across Python, C++, generated schemas, configuration, prompts, mock behavior,
tests, and user-facing documentation.

## Authority and document roles

Use these sources for distinct questions:

1. `STATUS.md` is the concise current-state snapshot: usable surfaces, known
   blockers, portable validation, and the boundary of live evidence.
2. `ARCHITECTURE.md` and accepted ADRs describe enduring authority boundaries
   and design decisions. They must call out current exceptions to an accepted
   design rather than presenting the intended design as implemented fact.
3. `schemas/` is generated from the current Pydantic models. Never hand-edit a
   schema or leave it stale after a model-description or wire-model change.
4. `docs/LIVE_VALIDATION_CHECKLIST.md` records dated Windows, build, install,
   and in-game evidence. Portable tests or a successful `SendInput` receipt are
   not substitutes for a resulting frame and fresh advancing telemetry.
5. `docs/ENGINEERING_LOOP_STATE.md`, dated incident reports, and old loop
   prompts are historical ledgers. Their local words such as "current" or
   "next" apply to their checkpoint unless a current-state document confirms
   them.

Code remains authoritative for what is implemented, but one layer of code is
not sufficient evidence for a cross-layer claim. A Python model can serialize a
request that the C++ consumer rejects; a capability can be advertised for a
path the executor cannot own; a portable fake can reproduce Python assumptions
without proving native conformance.

## Evidence vocabulary

Keep these states separate:

- **Declared** — a model, enum, contract, or configuration field exists.
- **Advertised** — the current planner payload offers it.
- **Serialized** — the producing side emits a valid shape.
- **Accepted end to end** — every consumer parses and admits the same shape.
- **Portable-tested** — deterministic tests pass without Kenshi.
- **Native-built** — the pinned Windows project compiled.
- **Installed** — the exact DLL hash is present in the Kenshi mod directory.
- **Live-proven** — the intended game effect appeared in a resulting frame and
  fresh, advancing authoritative telemetry.

Do not collapse these labels into "supported." Name the highest evidence level
actually established and the missing next boundary.

A causally later world revision proves that an observation is later than
dispatch. It does not, by itself, prove the intended effect. Until controller-
owned effect predicates exist, document which model-authored condition or
authoritative acknowledgement is being relied on and where false correlation
remains possible.

## Change checklist

For every action, telemetry, configuration, planner, or native change:

1. Trace the producer and every consumer across models, contracts, runtime,
   Python/native wire parsing, schemas, profiles, prompts, mock behavior, tests,
   and public docs.
2. State whether each configured value is enforced, metadata-only, duplicated,
   or currently dead. Do not describe a hard-coded default keymap as parsed
   `controls.cfg`.
3. Regenerate all schemas and require a byte-exact second export.
4. Keep registry counts literal. "Registered mechanism coverage" is not complete
   interaction, effect, mock, exit, coordinate-independence, or live coverage.
5. Put current facts in `STATUS.md`, enduring rules in architecture/ADRs, and
   dated experiments in the engineering ledger or live checklist.
6. Run drift searches, local-link validation, `git diff --check`, the portable
   tests, Ruff, and mypy. Report Windows/native/live validation separately when
   it was not performed.

This policy is intentionally stricter about evidence labels than about the
agent's in-game freedom. The purpose is faster integration with harder evidence,
not adding scenario-specific restrictions.
