# ADR: Read-only guide-grounded strategic advisor

Status: Accepted  
Date: 2026-07-26

## Context

The second 80-turn GPT-4.1 run was mechanically healthier than the first but
strategically repetitive. It approached the Mercenary Captain 13 times,
revisited unaffordable hire branches, and bought two Greenfruit while still
reporting zero food items. The playing model had current world evidence and
persistent memory, but no bounded way to ask a model with broader Kenshi
knowledge for a second opinion.

Giving a stronger model controller access would create a second action
authority and make causality harder to audit. Automatically injecting advice on
every turn would add cost and could replace the playing model's agency with a
hidden policy. Copying whole community guides into prompts would make source
drift and attribution difficult to inspect.

## Decision

Add `consult_advisor` as a planner-layer cognitive action with these boundaries:

1. It consumes one strategic action but creates no `WorldStateStore` command,
   never enters `AgentEnvironment.dispatch`, and emits zero keyboard, mouse, or
   native primitives.
2. A consult must be the only step in its plan and uses
   `success_conditions: []`. Its typed terminal result is owned by the advisor
   subsystem. The resulting brief appears in the next planner observation, not
   in already-authored future steps.
3. Every observation carries `advisor` availability. The playing model may ask
   whenever `may_request` is true. `suggested` becomes true on a deterministic
   cadence or when recent action signatures repeat; this is a cognitive signal,
   not an automatic hosted call.
4. Per-run call budgets, step cooldowns, and meaningful-state fingerprints
   suppress unchanged repeat requests before they reach the provider.
5. The advisor returns ranked goals, prerequisites, cautions, uncertainties,
   and source IDs. It cannot return `PlanEnvelope`, action kinds, targets,
   coordinates, bindings, or input instructions. The playing planner remains
   responsible for grounding any later action in current telemetry and
   authorable contracts.
6. Guide material lives in `knowledge/kenshi_strategy_v1.yaml` as concise
   derived claims. Each claim retains source title, creator/community where
   available, URL, source type, access date, confidence, and notes. Attributed
   sources include Reignswolf's *Kenshi Objective Guide (WIP)*, Kenshi wiki
   pages, and an r/Kenshi discussion that directly explains Greenfruit's
   ingredient status.
7. Provider output fails closed if any cited source ID is absent from the
   checked-in corpus. The final `AdvisorBrief` expands valid IDs back into
   attributions, so the player and the run log can inspect what supported the
   recommendation.
8. `config/live.longform.yaml` enables the advisor through OpenRouter with the
   independently overridable `KENSHI_AGENT_ADVISOR_MODEL`, currently defaulting
   to `openai/gpt-5.4`. The fast playing model remains independently configured.

## Consequences

- There is one physical action authority: the existing deterministic executor.
- Advice is available consistently without being forced into every planning
  round.
- A hosted failure, cooldown, exhausted budget, or unchanged state becomes a
  typed terminal receipt rather than an exception-driven retry loop.
- The advisor can still be wrong. Its brief is advisory guide knowledge, not
  current-world truth, and every recommendation must be verified against the
  next observation.
- The curated corpus is intentionally incomplete and requires maintenance as
  claims or sources change. Attribution makes that maintenance inspectable.
- A cognitive action can consume provider cost even though it changes no game
  state. Metrics therefore count advisor requests, hosted attempts, answers,
  suppressions, and failures separately from controller primitives and command
  receipts.

## Evidence boundary

Commit `3d4670f` provides portable end-to-end evidence: 502 tests pass; an
advisor consult reaches the next planner observation; unknown source IDs fail
closed; and its action receipt has zero primitives and no command ID. Ruff and
strict mypy pass, and generated schemas were byte-identical on a second export.

A real OpenRouter smoke against a synthetic 135-Cat, zero-food observation
returned `answered` from `openai/gpt-5.4`, ranked four near-term goals, and
resolved all five cited source IDs through the corpus. This proves the hosted
adapter and attribution seam, not live Kenshi integration. At the check
immediately afterward no Kenshi process existed and the last paused telemetry
was stale, so an in-game planner-request/next-action proof remains open.
