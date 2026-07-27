# 80-turn advisor signal — 2026-07-26

Write-once. Supersede by adding a later `REPORT_<date>_*.md`; never edit this
file.

Recovered 2026-07-27 from `docs/LIVE_GPT41_80_TURN_ADVISOR_REPORT_20260726.md`,
deleted at `f084056`. Three things are kept: the approaches-per-target table,
the option-success comparison against the prior run, and the reasoning that
produced the advisor design. Setup blocks, hashes, and narrative are dropped —
those live in the commit history and in `runs/`. The claims below are the
originals and are deliberately not re-derived or re-worded. If later evidence has
undercut one, that is a line in a *later* report, not an edit to this one.

Run `20260725T80turn-camera-recovery-live-02`: OpenRouter `openai/gpt-4.1`,
`native_assisted`, `continuous`, protocol `0.6.1`, 80 authorized steps.

Evidence class: historical evidence — supervised live Kenshi, 2026-07-26.

## The limitation this run isolated

Local execution is now materially more reliable than strategic selection: 13 of
20 dialogue approaches targeted the Mercenary Captain, repeatedly entering and
leaving bodyguard-hiring branches after the planner had already recorded that Hep
could not afford them. A bounded, read-only, source-grounded strategic advisor is
a better response than adding more controller macros or prompt prose.

## Option-success comparison against the prior run

The preceding 80-turn run completed only 3 of 30 monitored options (10%),
succeeded on 50 steps, failed on 30, and executed 58 receipts. This run completed
21 of 30 options (70%), succeeded on 68 steps, failed on 12, and executed 77
receipts. That is a substantial live execution improvement, although the
different world trajectory prevents treating it as a controlled benchmark.

| Measure | Prior run | This run |
|---|---:|---:|
| Monitored options completed | 3 / 30 (10%) | 21 / 30 (70%) |
| Plan steps succeeded | 50 | 68 |
| Plan steps failed | 30 | 12 |
| Action receipts executed | 58 | 77 |

## Strategic repetition

Dialogue approaches by target were:

| Target | Approaches |
|---|---:|
| Mercenary Captain | 13 |
| Barman | 4 |
| Pacifier | 2 |
| Metaru | 1 |

Repeated visible-control choices included:

- `3. Nothing`: six
- `3. Nevermind`: three
- `1. Nevermind`: three
- `1. I'm looking to hire some bodyguards`: three
- Barman trade-opening variants: four total

The planner repeatedly wrote objectives saying it could not afford mercenaries,
should end the conversation, or should seek another opportunity. It then
re-approached the same captain and reopened the same branch. Persistent memory
was present; what was missing was a stronger strategy model capable of turning
Kenshi knowledge and accumulated failure evidence into a materially different
next goal.

## The advisor design this argument produced

A suitable advisor boundary is therefore:

- read-only and unable to dispatch game/controller actions;
- explicitly requested by the playing planner, with an optional deterministic
  stall/cadence offer;
- grounded in a small curated guide corpus with source identity and excerpt
  provenance;
- given a compact current-world and recent-outcome digest;
- returning ranked goals, rationale, constraints, missing information, and
  cautions rather than a `PlanEnvelope`;
- subject to cooldown, per-run call/token budgets, and unchanged-state
  suppression;
- written to typed run events and exposed to later planner calls as advisory
  context, never silently merged into world truth.

The first deterministic stall signal should be repeated interaction with the
same target/branch after an explicit `no_op`, failed option, or memory saying
the branch is unaffordable or exhausted. A fixed low-frequency cadence can
make advice discoverable without forcing a call, but automatic dispatch should
remain separately configurable.

The resulting decision is recorded in [`ADR_STRATEGIC_ADVISOR.md`](ADR_STRATEGIC_ADVISOR.md).
