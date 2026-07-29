# ADR: evidence strength is independent from cross-layer consistency

Status: accepted (2026-07-28)

Supersedes [ADR_EVIDENCE_VOCABULARY](ADR_EVIDENCE_VOCABULARY.md). The earlier
decision delegated its runtime vocabulary to a temporary steering document.
This revision makes the durable decision self-contained.

## Decision

Every capability claim names its position on two independent axes. Neither
substitutes for the other, and the word "supported" is insufficient by itself.

### Runtime evidence

Use exactly one label:

- automated portable evidence
- deterministic live-shaped simulation
- Windows integration evidence
- native build/load evidence
- supervised live Kenshi evidence
- historical evidence
- proposed design

The labels are ordered only by what they prove. Code inspection is not a
runtime test; a compiled DLL is not a loaded DLL; a loaded DLL is not valid
telemetry; a model-produced action is not an executed action; an issued command
is not a successful command; and a current snapshot is not post-command proof
unless it is causally later.

This axis is a reporting discipline enforced by review. A portable test cannot
determine whether prose honestly describes the evidence behind it.

### Cross-layer consistency

A capability climbs four rungs:

| Rung | Meaning | Enforcement |
|---|---|---|
| declared | an action contract requires it | action contract registry |
| advertised | a producer emits it | capability consistency tests |
| serialized | it crosses a pinned wire document | capability consistency tests |
| accepted end to end | the far side honours it | supervised live run |

Advertisement and serialization are portable gates. End-to-end acceptance is
not: it requires Kenshi and the native plug-in to be running. Reaching one rung
never implies the next.

## Consequences

Reports carry both labels. A compiled and serialized capability can still be
explicitly described as lacking live end-to-end acceptance. Generated manifests
and tests remain the source of current capability membership; this ADR owns only
the stable vocabulary used to describe the evidence.
