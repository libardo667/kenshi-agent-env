# ADR: declared scenarios own recurrence evidence

Status: accepted 2026-07-27. The affordance-demand subsystem it governed was
removed once game-derived parity denominators replaced model-reported demand.

## Context

An affordance request recurring in several run IDs may still be one save and one
situation replayed several times. Counting those IDs as independent evidence
inflates confidence precisely where the capability flywheel needs diversity.
Inferring situation identity from a run name, save label, telemetry, or prose
would replace that inflation with guesswork.

The deliberate Kenshi matrix currently varies indoor/outdoor, hostile/safe,
broke/funded, solo/squad, and day/night situations.

## Decision

A run may declare one complete scenario identity:

- an operator-assigned stable `scenario_id`;
- an operator-assigned `save_id` for the exact reproducible save snapshot; and
- one value on each matrix axis.

The declaration is all-or-nothing and is written into `run_started`. It is
experimental metadata, not an observation exposed to the playing model and not
a claim that telemetry independently verified those labels.

Offline aggregation keeps raw run and request counts, but cross-scenario
recurrence is counted and ranked only by distinct declared
`(save_id, scenario_id)` pairs. Rerunning one pair does not increase that count.
Historical, missing, malformed, or conflicting declarations remain visible as
undeclared runs and contribute no cross-scenario or cross-save evidence. Reusing
one pair with different axis values invalidates every declaration of that pair.

Survival-critical demand remains review-worthy even from one undeclared run.
Scenario recurrence ranks candidates for engineering review; it never promotes
an action or grants authority.

## Consequences

`run_id` measures executions; `scenario_id` measures situations; `save_id`
measures reproducible world starting points. Reports expose all three so repeat
reliability cannot masquerade as generality.

The supported `run` and `journey` entrypoints carry the declaration. Future
matrix runs can be selected from measured coverage instead of filenames or
hand-maintained narratives.
