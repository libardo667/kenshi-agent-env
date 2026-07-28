# Changelog

## Unreleased

- Separated continuity into three authorities that cannot blur. Action and plan
  outcomes are runtime-owned working history with stable `ao-`/`po-` IDs and
  full plan/step/command provenance; a plan outcome carries the objective it
  set out to do and why it ended. Durable memory is reached only through
  `ContinuityAuthority`: facts and episodes must cite resolvable evidence IDs,
  the stored grounding is rendered by the runtime rather than authored, a
  `target_id` must be in the current observation, and every operation returns an
  accepted/rejected/no-op receipt that fails independently. Plans commit after
  validation, single-step decisions after their receipt, and patches only when
  the exact patch is applied — `PlanPatch` continuity was previously dead
  schema. Renamed `memory_writes` to `continuity_operations` with a `keep`
  discriminator. Removed the automatic "Set out to…" durable episode, which was
  a claim about unfinished work filed as an event. Recall no longer writes:
  ordering uses declared salience and creation time, and `last_delivered_at`
  (nullable, migrated as `NULL` from the old `last_accessed_at`) is recorded
  only where a planner payload is actually assembled.
- Added bounded continuous planning: typed plans and future-only patches,
  causal revisions, valued state deltas, option lifecycle, persistent plan
  memory, an independent safety supervisor, and final input-lease
  revalidation.
- Added the generic `dialogue_interaction_v1` live policy and thirteen reusable
  semantic action contracts for dialogue approach, exact reviewed world-object
  tasks, local movement, visible controls, screen dismissal, buying, selling,
  equipping, game bindings, camera recovery, and scrolling. Retired the fixed
  `food_procurement_v1` policy after lifting its useful reference and purchase
  guarantees into the generic catalog.
- Added an open-ended supervised long-form profile that can leave the world
  running, overlap strategic planning with movement, hand control to the human,
  and resume from a fresh plan after a visible resettable takeover countdown.
- Expanded native protocol `0.5.0` with split title/loaded lifecycles, stable
  identities, management and inventory UI, named item cells, window ownership,
  squad nutrition/blood/inventory/combat facts, camera bearings, and up to 224
  current UI controls.
- Generalized the native-assisted bridge to exact dialogue approach and
  exact-character walking. Protocol `0.6.0` completes the targetless bounded
  directional path: command-specific request/acknowledgement identity, keyed
  option ownership, exact active-order adoption, and shared Python/C++ golden
  fixtures enforced by the native build. Protocol `0.6.1` corrects the
  paused-start handoff and destination-crossing completion semantics; the
  installed DLL passed an exact live acceptance/completion smoke with plausible
  movement, a resulting frame, and safe final pause.
- Added protocol `0.7.0` indoor telemetry and the no-argument
  `exit_current_building` contract. Native code resolves the current building's
  unlocked door and outside point; a keyed monitored option owns paused-start
  unpause, completion, and the shared no-progress terminal. A live Storm House
  exit completed with `outside_door_destination_reached` even though Kenshi's
  indoor handle lingered after visible traversal.
- Added protocol `0.8.0` exact contextual world targets and the
  `perform_context_action` contract. The first reviewed operation identifies a
  natural resource, rechecks its native default task and availability, issues
  `OPERATE_MACHINERY` to that exact handle, and completes only when Kenshi's AI
  reports the exact task/subject pair. Python/C++ fixtures, the pinned native
  build, and the byte-identical installed DLL pass; live target discovery and
  effect remain deliberately unclaimed. Protocol `0.8.1` keeps structurally
  recognized mines observable when their current task is unavailable, preserves
  fail-closed binding and dispatch, and warns when the bounded building scan may
  be incomplete.
- Adopted a right-sized native-integration boundary: faithful semantic Kenshi
  coverage, truthful capabilities, and reviewable evidence determine plugin
  scope rather than a small/medium/large size target.
- Added OpenRouter structured planning with provider routing and a local-schema
  fallback, mode-aware OpenAI output budgets, dynamic observation/control
  budgeting, and a fast non-reasoning long-form planner default.
- Corrected hunger semantics across the mock, heuristic planner, sample
  telemetry, and guide-grounded advisor to match native Kenshi's 0.0-to-3.0
  nutrition reserve. The advisor now receives authoritative 2.5/2.0/1.0
  eating, malnutrition, and fainting thresholds plus the layered-HUD caveat.
  A repeated live consultation changed "urgent starvation" into a conservative
  near-term food goal while preserving town-safety and combat-avoidance advice.
- Added the zero-input `request_affordance` planner action so a live playing
  model can retain a grounded missing capability, blocked goal, evidence,
  workaround, and urgency. Requests survive advancing telemetry, duplicates are
  suppressed, receipts and metrics stay typed, and recording one never grants
  the missing tool.
- Added semantic launcher controls, reversible graphics-profile verification,
  duplicate-client/Steam/memory/renderer checks, relative-pointer
  synchronization, window-scoped controls, and exact close-box derivation.
- Added compact observation logs, a click-through capture-excluded lifecycle
  overlay, a persistent readable `transcript.log`, fact/affordance audits, and
  planner-visible purchase/camera/movement outcome conditions. These later-state
  conditions are not yet a controller-owned general effect-verification engine.
- Regenerated every checked-in JSON Schema from the current models, adding the
  expanded action catalog, movement request fields, inventory/combat/UI facts,
  and current plan/receipt structures while removing the retired inspect action.
- Added `uv.lock`. The current portable baseline is 541 passing tests with
  clean Ruff and strict mypy over 58 source files.

## 0.1.0 — scaffold

- Added a deterministic mock agent environment and one-day survival baseline.
- Added strict telemetry, action, observation, decision, receipt, and memory
  models with generated JSON Schemas.
- Added heuristic, scripted, subprocess, and optional OpenAI vision planners.
- Added SQLite memory, JSONL logging, replay environment, and run metrics.
- Added a guarded Windows SendInput controller and client-area screenshot capture.
- Added a read-only KenshiLib telemetry plugin skeleton with atomic JSON output.
- Added live validation, protocol, experiment, safety, and coding-agent guides.
