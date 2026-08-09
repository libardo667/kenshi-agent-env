# Reverse-engineering evidence

Each directory below this one is one canonical research object. Copy
`_template/`, rename it for the subsystem, and replace every placeholder.
The six-file boundary is deliberate:

- `question.md` fixes the question before probing;
- `static_evidence.md` records declarations, executable inspection, and source
  facts without turning them into runtime claims;
- `call_sites.json` pins symbols, RVAs, inferred signatures, confidence, and
  repository call sites to exact binary and library fingerprints;
- `dynamic_observations.json` keeps live probes, crashes, contradictions, and
  tests as separate observations, including missing evidence explicitly;
- `abi_notes.md` records calling convention, layout, ownership, and hook risks;
- `conclusion.md` is the reviewed disposition with separate source-proven,
  test-proven, live-proven, and withheld sections.

Run `python scripts/check_research_evidence.py` after editing. The full
`./dev verify-portable` gate runs the same validator, regenerates
`docs/generated/RESEARCH_EVIDENCE_INDEX.md`, and rejects stale output.

Start a repository investigation with the
[reverse-engineering evidence issue form](../../.github/ISSUE_TEMPLATE/reverse-engineering-evidence.yml),
which asks the six review questions before implementation. The generated
[research index](../../docs/generated/RESEARCH_EVIDENCE_INDEX.md) lists accepted
packages and their proof dispositions. Machine-readable contracts live in the
three `research_*.schema.json` files under `schemas/`.

The package is the reverse-engineering authority. Operation proof ledgers may
classify behavior and link to a package, but must not copy its argument. A
request return or acknowledgement is delivery evidence only; a live conclusion
requires later engine state in an exact run bundle.
