# Changelog

## Unreleased

- Added bounded continuous planning: typed plans and future-only patches,
  causal revisions, valued state deltas, option lifecycle, persistent plan
  memory, an independent safety supervisor, and final input-lease
  revalidation.
- Added the generic `dialogue_interaction_v1` live policy and eleven reusable
  semantic action contracts for dialogue approach, local movement, visible
  controls, screen dismissal, buying, selling, equipping, game bindings, and
  scrolling. Retired the fixed `food_procurement_v1` policy after lifting its
  useful reference and purchase guarantees into the generic catalog.
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
- Added `uv.lock`. The current portable baseline is 532 passing tests with
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
