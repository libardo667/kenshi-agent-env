# Implementation status

Current as of 2026-07-26. This is the current-state snapshot; dated proofs and
superseded milestones remain in `docs/ENGINEERING_LOOP_STATE.md` and
`docs/LIVE_VALIDATION_CHECKLIST.md`.

## Current system

- The portable baseline is a deterministic Kenshi-like environment with strict
  Pydantic models, analyzable JSONL lifecycle logs, optional full-observation
  replay, SQLite memory, generated schemas, and
  heuristic, scripted, subprocess, OpenAI Responses, and OpenRouter planners.
- `single_step` remains the default scheduler. Feature-flagged `continuous`
  planning accepts bounded typed plans and future-only patches, owns action and
  risk budgets, rechecks typed conditions before every step, and evaluates
  model-authored success conditions only on causally later observations. That
  prevents pre-action state from passing, but is not yet controller-owned proof
  of the intended effect.
- Continuous runs have one authoritative observation pump and bounded
  `WorldStateStore`. The store preserves revisions, semantic deltas with old and
  new values, transient events, entity lifetimes, active plan/command state,
  visual carry-forward, and isolated subscriber queues.
- An independent deterministic supervisor can preempt blocked planning or
  execution on stale/stalled telemetry, capability loss, threats, human input,
  F12, or an unauthorized unpause. Cleanup is successful only after a later
  observation confirms the safe state.
- Every continuous step carries an `ExecutionToken`. After the polite Windows
  input lease is acquired and immediately before the first primitive, the live
  environment re-reads canonical state and revalidates the plan assumptions,
  step preconditions, control mode, calibration, human-input evidence, and
  emergency-stop evidence. Lost authority emits zero input.
- Human input cancels the active plan and hands control over visibly. The
  long-form profile may restore an originally running world after a quiet,
  resettable takeover countdown; F12 disarms automatic takeover for the run.
- The long-form profile has a read-only guide-grounded strategic advisor.
  `consult_advisor` consumes one strategic turn but creates no world command and
  emits zero controller primitives. Every observation carries availability,
  cadence/repetition suggestion, cooldown, budget, and the latest attributed
  brief. Unknown source IDs fail closed, and unchanged-state requests are
  suppressed before the provider call.

## Planner-visible action surface

The generic `dialogue_interaction_v1` policy now governs a reusable contracted
action catalog rather than a fixed dialogue recipe. Its name is historical; the
policy is the current generic semantic live policy.

The current catalog advertises these contracts when their declared capability
requirements are present:

- `approach_dialogue_target` — approach one exact current non-hostile dialogue
  target and open dialogue through a monitored native-assisted option.
- `move_to_character` — walk to one exact nearby character without opening
  dialogue.
- `move_in_direction` — walk a bounded bearing/distance without naming a
  person. Its command identity is command ID, selected character, bearing, and
  distance; a keyed monitored option owns it until native completion.
- `exit_current_building` — take no door, direction, or coordinate arguments;
  native code resolves the selected character's current unlocked exit and a
  keyed option owns the order through completion or bounded pathing failure.
- `activate_visible_control` — activate one unique current semantic UI control.
- `dismiss_screen` — close a bound trade or inventory window through its own
  current close box. Dialogue ends by activating an exact visible closing
  reply; Escape is deliberately refused because it opens the ESC menu.
- `purchase_item`, `sell_item`, and `equip_item` — bind an operation to one
  exact named item cell, owner window, and current trade state. Purchases and
  sales must prove that money changed; equip refuses while trade is active.
- `use_game_binding` — use an allowlisted reversible Kenshi binding for screens,
  time, camera, selection, or stopping movement.
- `scroll_screen` — reveal contents beyond the currently rendered part of one
  exact open window.
- `recover_camera_view` — take no model-authored camera parameters; bind one
  selected character and the current world HUD, then let the controller own a
  bounded follow/floor/zoom/orbit/tilt transaction and typed frame-scored verdict.

Planner-layer control (`noop`, `wait`, `pause`, `set_speed`, whole-run `stop`,
and read-only `consult_advisor`) remains separate from game-object contracts.
Raw keys, hotkeys, cursor moves, clicks, and scroll primitives are controller
implementation details and are not planner-authorable on the generic live
path.

## Live profile and observed behavior

- `config/live.longform.yaml` is the open-ended supervised profile:
  `native_assisted`, `continuous`, `dialogue_interaction_v1`, an unpaused world,
  persistent plan memory, the generic contracted action catalog, the bounded
  OpenRouter advisor, and explicit live/native/continuous acknowledgements.
- `config/live.dialogue.yaml` is the shorter stop-motion proof profile.
- `config/live.burnin.yaml` is a legacy single-step calibrated profile. Its
  former `food_procurement_v1` policy is retired and its continuous policy is
  explicitly `disabled`.
- The retired food policy's useful guarantees were lifted into generic
  contracts: exact reference binding, in-lease revalidation, at-most-once
  purchases, seller/owner checks, and causal money verification.
- Supervised runs have proved generic approach and dialogue activation,
  semantic startup, inventory/trade navigation, one-step purchases, readable
  world deltas, persistent continuous memory, and human-control handback.
  Selling and equipping have guarded contracted implementations and portable
  coverage grounded in the observed right-click semantics. One recorded
  long-form run bought Greenfruit and a first-aid kit with both debits confirmed.
- Native walking supports exact-character destinations, a targetless bounded
  direction/distance order, and a no-argument current-building exit. The exit
  resolves an unlocked door and outside destination natively; completion
  accepts stable outdoor membership or tightly reaching that resolved point
  after meaningful movement. All movement orders have a shared ten-second
  continuous-unpaused no-progress terminal, so a blocked order cannot poison
  later movement. Protocol `0.7.0` has live load, fresh telemetry, exact request
  identity, explicit cancellation/stall proof, and bounded completion proofs.
  Run
  `20260725T2223-direction-smoke-061-green` moved Hep about 30.4 world units,
  completed the exact keyed order with `walk_destination_reached`, captured a
  changed resulting frame, and left Kenshi paused with no active command.
- Run `20260726Tnative-building-exit-live-04` issued one parameterless exit
  request from a paused indoor start, moved Hep about 155.58 x/z units through
  the Storm House door, completed at telemetry sequence 506 with
  `outside_door_destination_reached`, succeeded at the monitored option and
  plan layers, and auto-paused at sequence 508. The user directly observed Hep
  outside even though Kenshi retained `indoors=true`; that flag authorizes the
  start but is not the sole terminal. The full correction history and evidence
  boundary are in `docs/LIVE_NATIVE_BUILDING_EXIT_REPORT_20260726.md`.
- Run `20260725T80turn-gpt41-live-01` completed an 80-step GPT-4.1 live action
  budget in 15m46.369s. It published 5,046 observations with zero stale
  observations, zero input-boundary rejections, and zero safety preemptions;
  executed 58 receipts; approached and traded with the Barman; and remained
  process/renderer stable. Its terminal is intentionally `success=null`.
  Camera recovery consumed the first 30 executed receipts, and a later
  nonterminal native walk left movement poisoned until an explicit post-run
  safety pause cancelled it. The full report is
  `docs/LIVE_GPT41_80_TURN_REPORT_20260725.md`.
- Run `20260725T80turn-camera-recovery-live-02` completed a second 80-step
  GPT-4.1 live budget in 18m18.624s. It retained 5,595 fresh observations,
  executed 77 actions, succeeded on 21/30 monitored options, moved Hep about
  388.04 net x/z units into The Hub bar, and causally confirmed two 22-cat
  Greenfruit purchases. Three no-argument camera requests returned live
  `recovered` at scores 0.732, 0.724, and 0.784. Its strategic limitation was
  equally clear: 13/20 dialogue approaches targeted the Mercenary Captain and
  repeatedly revisited unaffordable hire branches. The full evidence and
  bounded read-only advisor design signal are in
  `docs/LIVE_GPT41_80_TURN_ADVISOR_REPORT_20260726.md`.
- Run `20260725-camera-recovery-live-02` exercised the completed no-argument
  controller transaction against Hep's still-obstructed ruined Storm House
  view. It advanced fresh telemetry 24,999→25,041, established a zero-distance
  character anchor, retained ten floor/zoom/orbit/tilt candidates, selected the
  best frame, left Kenshi paused with no active command, and returned the
  truthful bounded terminal `failed_after_bounded_attempts` rather than
  exposing more camera gestures to the model.
- The advisor implementation has portable end-to-end proof, a synthetic
  provider smoke, and a live in-game cognitive-action proof. Run
  `20260726Tlive-survival-advisor-01` created no world command and zero
  primitives, but exposed that the advisor did not know native hunger units and
  called 2.47 urgent starvation. The runtime now supplies the authoritative
  0.0-to-3.0 reserve and 2.5/2.0/1.0 eating, malnutrition, and fainting
  thresholds; mock and heuristic producers use the same units. Repeated run
  `...advisor-02` correctly interpreted 2.47 as about 247/300: a prudent
  near-term food constraint, not immediate danger. It retained source
  attribution, town-safety advice, and combat avoidance. A live multi-turn
  advisor-to-next-planner behavioral handoff remains separate open evidence.
- The decision overlay is capture-excluded and click-through. Each run also
  keeps typed lifecycle evidence in `events.jsonl`. A selectable
  `transcript.log` is intended, but the 80-turn run did not produce one.

## Native protocol 0.7.0

The native plugin hooks Kenshi-owned title and loaded-game update points and
atomically replaces a complete snapshot at roughly two hertz.

Current loaded-game telemetry includes:

- pause, speed, money, elapsed game time, camera position, and camera-relative
  bearings;
- stable session-scoped squad, selection, nearby-character, dialogue-target,
  and native-command identities;
- squad life/consciousness/down/crippled/combat state, position, movement,
  reported indoor-building membership, nutrition reserve (`hunger`), blood,
  and bounded inventory/equipment facts;
- dialogue, trade, inventory, stats, and management-window state, including the
  active management tab;
- exact dialogue target/options, tooltip state, shop ownership, and up to 224
  visible buttons, named item cells, and text controls with window ownership and
  normalized bounds;
- a bounded keyed acknowledgement ring for four declared native commands:
  approach a dialogue target, move to an exact nearby character, and move a
  bounded bearing/distance, or exit the selected character's current building.
  Direction acknowledgements carry the empty target plus bearing and distance;
  targeted commands carry a stable target and zero direction fields; building
  exit carries neither a target nor a model-authored vector.

The DLL is therefore not globally read-only. `interface_only` removes native
command capabilities and acknowledgement state before planning and rejects
native actions again at the guard and environment boundaries. `native_assisted`
requires configuration opt-in and a separate CLI acknowledgement.

## Safety and operational boundaries

- Live input remains dry-run unless configuration enables it and the CLI
  receives `--execute-live-actions`.
- Native-assisted runs additionally require
  `--acknowledge-native-assisted-control`; live continuous runs additionally
  require `--acknowledge-continuous-live`.
- The controller uses a narrow Kenshi window match, equal-integrity Windows
  input, polite leases, F12, rate and primitive limits, calibration identity,
  semantic current bounds, and at-most-once native command IDs.
- Quicksave, quickload, editor, navmesh rebuild, biome reload, arbitrary
  internal tasks, teleportation, and direct health/money/faction mutation are
  deliberately absent.
- Renderer resets on the development host were traced to the older Intel Iris
  Xe driver, not to the reduced graphics profile. Driver `32.0.101.7088` passed
  two dissimilar 20-minute soaks; multi-hour and literal large-water coverage
  remain open.

## Verified portable baseline

For the protocol `0.7.0` building-exit slice on 2026-07-26:

- `pytest -q`: **532 passed**.
- `ruff check .`: passed.
- `mypy --strict src/kenshi_agent`: passed across **58 source files**.
- Generated schemas were refreshed from the current strict models.
- The pinned VS2010 SP1 `Release | x64` DLL and native conformance executable
  passed; built, staged, and installed 205,824-byte artifacts were
  byte-identical at SHA-256
  `2110dcf73421a5919e5c3f0efb44cdd9929946a0902aa09d8662191cd94ba8d9`.
- The guarded preflight passed, live protocol `0.7.0` telemetry remained fresh
  and advancing, and the exact visible building-exit run completed safely.

## Open work

- Retain one live plan in which the playing model authors `consult_advisor`, the
  receipt records no command ID and zero primitives, the next planner
  observation contains the attributed brief, and its subsequent changed goal
  is grounded in both that brief and current Kenshi evidence. Portable and
  synthetic hosted proofs do not satisfy this boundary.
- `Character::isIndoors()` can remain valid after Hep is visibly outside the
  Storm House bar. Do not use it as the sole exit terminal; broader buildings,
  doors, and zone transitions still need explicit live coverage.
- Targetless local directional movement has one exact live proof at a
  36.5-degree bearing and 30-unit distance. Broader bearings, distances,
  obstacle layouts, and scenes remain unproven rather than inferred from that
  run. Chosen remote map travel has no semantic action at all;
  `move_to_character` remains bounded to the nearby-character query.
- Management screens are observable and navigable, but their domain contents
  and operations are not comprehensively modelled.
- `active_screen` still names only title/world/inventory/dialogue/trade;
  management and stats state live in their dedicated fields.
- UI export is bounded at 224 native entries and by the planner's real context
  ceiling. Busy screens may still require closing or scrolling a window.
- Item cells export base `item_value`, not an authoritative shop asking price.
  Optional pre-purchase price/balance gates therefore operate on the declared
  estimate; the actual debit is known only from the causally later money
  change.
- The generic policy requires planner-authored success conditions on later
  revisions, but action contracts do not yet derive authoritative effect
  predicates from pre-action state. A later, correlated change can therefore be
  accepted as the intended effect for many action kinds. In particular,
  `camera.position` is a capability condition rather than camera coordinates,
  so its later presence does not prove that the camera moved.
- `use_game_binding(pause)` bypasses `allow_live_unpause_actions=false`, whose
  guard applies only to direct `PauseAction`. The binding map is a hard-coded
  copy of Kenshi defaults, not a parser for the user's active `controls.cfg`.
- Only safety-supervisor preemption owns causally verified final pause cleanup.
  Normal stop, objective completion, budget/replan exhaustion, exceptions, and
  cancellation end through `LiveEnvironment.close()`, which is a no-op.
- Run metadata does not persist the resolved provider/model route, and
  `transcript.log` can be absent despite the prior public claim. The README now
  treats JSONL as authoritative. The summarizer also reported
  `planner_errors=0` for the 80-turn log even though it contains four top-level
  `planner_error` events. Two late invalid condition paths emitted Pydantic
  enum-serialization warnings only to the console.
- The mock environment meaningfully models only a subset of the current
  semantic catalog; newer movement, trade, equip, scrolling, and binding paths
  mostly rely on focused fakes rather than one representative end-to-end mock
  state machine. It also reports `StopAction` success only after the one-day
  survival horizon, conflating an intentional run termination with benchmark
  objective success.
- Four configured fields are currently parsed but behaviorally unused:
  `runtime.stop_when_terminated`, `planner.temperature`,
  `capture.crop_client_area`, and `safety.require_cli_execute_flag`. The runtime
  always owns termination, hosted adapters omit temperature, capture already
  uses the client area, and the CLI gate is unconditional.
- Default logs use compact observation digests. They support lifecycle metrics
  and summaries but not `ReplayEnvironment`; full environment replay requires
  `runtime.log_full_observations: true` and substantially larger logs.
- Provider-neutral strict schema compilation currently imports private
  `openai.lib._pydantic` code, and two schema tests import it at collection
  time. The playing-planner OpenRouter adapter also does not apply its
  configured output-token budget or temperature; the advisor adapter does apply
  its separate output-token ceiling.
- Sale consumes the risk field named `purchase_actions`; capability aliases are
  represented as one flat set rather than aliases per required capability; and
  condition normalization silently changes some planner-authored shapes. These
  are current contract semantics, not polished public abstractions.
- The contract policy knows scrolling and non-toggle bindings can repeat, but
  the shared plan validator currently accepts retry budgets only for run-control
  actions. Semantic repeats must be authored as explicit later steps.
- `bleeding_rate`, body-part wounds, imprisonment/enslavement, getting-eaten,
  current task/goal, location name, trader money, geometry occlusion, and broad
  world/map knowledge remain unavailable or unvalidated.
- Broader identity and safety validation is still needed across recruit,
  dismiss, reorder, KO, death, save/load, and zone transitions.
- Live evidence is version- and host-specific. Alternate resolution, repeated
  focus/input trials, multi-hour stability, and broad unsupervised strategy
  competence are not established.
- There is a checked-in `uv.lock`, but no CI workflow.
- The shared Python/C++ golden request and acknowledgement fixtures prove wire
  shape, but they do not exercise native update timing. The first live
  direction probe exposed exactly that remaining integration class.
