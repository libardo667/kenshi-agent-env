# ADR: Two evidence axes, never collapsed into "supported"

Status: accepted
Date: 2026-07-27

## Context

"Supported" is one word doing two jobs, and both of them are about how much a
claim is worth. A claim can be weak because nothing stronger than code inspection
backs it, and it can be weak because the layers disagree about it. Those are
independent, and a claim can be strong on one axis while failing on the other.

Commit `8993b10` is the case that forced the distinction. Protocol 0.8.1
advertised `squad.indoors`, serialized `indoors: true`, and had supervised live
telemetry showing it — strong on the runtime axis. The native command fence still
rejected the matching exit as `not_indoors`. There was no vocabulary for "every
layer emits this and the last one does not honour it", so it was reported as
supported until a live run said otherwise.

Both axes were previously recorded in `DOCUMENTATION_TRUTH.md`. That document
described current state in prose, drifted within a week, and was deleted in the
2026-07-26 cleanup. The runtime axis survived as `LOOP_PROMPT.md` §11. The
consistency axis did not survive at all, which is how this ADR came to exist.

## Decision

Two axes are recorded separately. Neither substitutes for the other, and no
claim is labelled "supported" without naming its position on both.

### Axis 1 — runtime strength (what backs the claim)

`LOOP_PROMPT.md` §11 is the normative list. Every claim carries exactly one of:
automated portable evidence; deterministic live-shaped simulation; Windows
integration evidence; native build/load evidence; supervised live Kenshi
evidence; historical evidence; proposed design.

The ladder beneath it is the operative rule: code inspection is not a runtime
test, a compiled DLL is not a loaded DLL, a loaded DLL is not valid telemetry, a
model-produced action is not an executed action, an issued command is not a
successful command, and a current snapshot is not post-command proof unless it
is causally later.

**This axis is enforced by review, not by code.** No test can tell whether a
sentence in a report is labelled honestly. Mislabelling here is caught by a
reader or not at all.

### Axis 2 — cross-layer consistency (how far the claim travels)

A capability climbs four rungs, and the rung it stops at is the claim's real
strength:

| Rung | Meaning | Enforced by |
|---|---|---|
| declared | a contract names it in `required_capabilities` | `ACTION_CONTRACTS` |
| advertised | a producer emits it in a telemetry capability list | `tests/test_capability_consistency.py` |
| serialized | it crosses the wire in a pinned document | `tests/test_capability_consistency.py` |
| accepted end to end | the far side honours what it advertised | supervised live run |

`tests/test_capability_consistency.py` is the enforcement point for the two
middle rungs. It fails when a contract requires a capability no producer emits,
when a native capability authorizes a wire command no fixture pins, and when a
contract offered in `interface_only` needs state the mock never advertises.
Native advertisement comes from `GameplayCapabilities.json`; a generated-header
staleness gate keeps that manifest compiled into the plug-in without scraping
C++ source spelling.

The fourth rung is not portably reachable — proving the far side honours its own
advertisement requires the far side to be running. `8993b10` lived and died
there. That gap is the reason this axis is written down rather than assumed.

## Consequences

- A capability that reaches "serialized" and stops has a name, so the next
  instance has a label to report it under instead of rounding it to "supported".
- The consistency axis is a test rather than a paragraph. Prose describing
  current state drifts; that is what happened to the document this replaces, so
  restoring it as prose would have repeated the failure exactly.
- The runtime axis stays prose, because it is a labelling discipline and there
  is nothing to execute. It is recorded here as a known unenforced boundary
  rather than left implicit.
- `MOCK_UNEXERCISABLE` in the consistency test names the contracts the mock
  cannot satisfy. It shrinks only. An entry is a hole that is visible instead of
  a hole that is silent.
- Reports carry both labels. `docs/REPORT_*.md` is the shape that holds analysis
  across runs; a report claiming a capability works states which rung it reached
  and what backed it.
