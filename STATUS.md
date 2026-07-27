# Implementation status

Current-state snapshot. Dated evidence lives in `git log` and `runs/<run-id>/`;
the action surface lives in the generated [catalog](docs/generated/ACTION_CATALOG.md)
and [coverage](docs/generated/UI_AFFORDANCE_COVERAGE.md).

## What works

- A deterministic Kenshi-like mock environment with strict Pydantic models, JSONL
  lifecycle logs, optional full-observation replay, SQLite memory, and
  heuristic/scripted/subprocess/OpenAI/OpenRouter planners.
- `single_step` is the default scheduler. Feature-flagged `continuous` planning
  accepts bounded typed plans and future-only patches, owns action and risk
  budgets, and rechecks typed conditions before every step. See
  [ADR: bounded continuous planning](docs/ADR_CONTINUOUS_PLANNING.md).
- One authoritative observation pump and bounded `WorldStateStore` preserving
  revisions, semantic old/new deltas, transient events, entity lifetimes, active
  plan/command state, and isolated subscriber queues.
- An independent deterministic supervisor that preempts on stale or stalled
  telemetry, capability loss, threats, human input, F12, or unauthorized unpause.
  Cleanup succeeds only after a later observation confirms the safe state.
- One idempotent final-state owner runs for normal completion, stop, budget,
  failure, cancellation, and exception exits. Executed live runs return a
  distinct failure code unless fresh telemetry confirms pause; cleanup input
  requires a causally later confirmation.
- A final in-lease authorization fence: after the lease is acquired and just
  before the first primitive, canonical state is re-read and plan assumptions,
  preconditions, control mode, calibration, and human-input and emergency-stop
  evidence are revalidated. Lost authority emits zero input.
- Human input cancels the active plan and hands control over visibly. The
  long-form profile may restore a running world after a quiet, resettable
  takeover countdown; F12 disarms automatic takeover for the run.
- A read-only guide-grounded strategic advisor. `consult_advisor` consumes a
  strategic turn, creates no world command, and emits zero primitives. Unknown
  source IDs fail closed; unchanged-state requests are suppressed before the call.
- `request_affordance` lets the playing model report a grounded missing control.
  It emits no input and grants no capability; later observations carry the
  request so the run can take a safe workaround meanwhile.

## Live profiles

- `config/live.longform.yaml` — open-ended supervised profile: `native_assisted`,
  `continuous`, `dialogue_interaction_v1`, unpaused world, persistent plan
  memory, the contracted catalog, the bounded advisor, explicit acknowledgements.
- `config/live.dialogue.yaml` — shorter stop-motion proof profile.
- `config/live.burnin.yaml` — legacy single-step calibrated profile; its former
  `food_procurement_v1` policy is retired and its continuous policy is
  `disabled`.

From WSL, `./dev journey` preserves exact planner argv; `./dev crash` archives terminal evidence before optional bounded dismissal.
Supervised runs have demonstrated generic approach and dialogue activation,
semantic startup, inventory and trade navigation, one-step purchases with
confirmed debits, readable world deltas, persistent continuous memory, and
human-control handback. Selling and equipping have guarded implementations and
portable coverage but no completed live proof. Native walking supports
exact-character destinations, a targetless bounded direction order, and a
no-argument building exit; all movement orders share a ten-second
continuous-unpaused no-progress terminal, so a blocked order cannot poison later
movement. During exact native commands, engine-native camera follow is reasserted
each frame and live-proven through one short obstructed move.

## Native protocol 0.8.2

The plug-in hooks Kenshi-owned title and loaded-game update points and atomically
replaces a complete snapshot at roughly 2 Hz. Telemetry covers pause, speed,
money, game time, camera position and bearings; stable session-scoped squad,
selection, nearby-character, dialogue-target, world-target and command
identities; squad life/consciousness/combat state, position, resolved indoor membership,
nutrition reserve, blood, and bounded inventory; dialogue, trade, inventory,
stats and management window state; up to 224 visible controls with window
ownership and normalized bounds; up to 128 structurally reviewed natural
resources from a 2,000-unit query with current task eligibility reported
separately; and a keyed acknowledgement ring for five declared commands.

The DLL is therefore not globally read-only. `interface_only` removes native
command capabilities before planning and rejects native actions again at the
guard and environment boundaries. `native_assisted` requires configuration
opt-in plus a separate CLI acknowledgement. Wire semantics are in
[`GUIDE_TELEMETRY_PROTOCOL.md`](docs/GUIDE_TELEMETRY_PROTOCOL.md).

## Open work

- **Protocol `0.8.2` is live-loaded with advancing telemetry, honest outdoor
  state, and structural resource targets.** Contextual operation remains unproven:
  every observed target was unavailable, so no `operate` order was authorized.
- No live plan has yet been retained in which the playing model itself authors
  `consult_advisor` and its changed goal is grounded in both the attributed
  brief and current Kenshi evidence. Synthetic proofs do not satisfy that.
- `request_affordance` carries a free-text `capability`, so requests from
  different runs cannot be aggregated or ranked. A controlled vocabulary is
  needed before fanning out across multiple saves.

## Known limitations

- Native build and supervised evidence are specific to the pinned
  Kenshi/RE_Kenshi/KenshiLib versions and the current Windows host.
- Live proofs are single supervised runs. One direction probe and one Storm House
  exit do not generalize; raw `Character::isIndoors()` can retain a stale handle.
  The producer fails unresolved buildings closed, while exit completion uses
  controller-owned proof. Chosen remote map travel has no semantic action.
- Item cells expose base value, not the final shop charge; the real debit is
  confirmed only after the at-most-once purchase, from later money telemetry.
- A causally later observation stops stale pre-action state from satisfying a
  postcondition, but most success conditions are still planner-authored. Only
  `controller_verified` contracts carry controller-owned effect proof, so later
  correlated state can still be mistaken for the intended effect.
- `use_game_binding(pause)` can toggle an unpaused game even when
  `allow_live_unpause_actions=false`, because that guard applies only to direct
  `PauseAction`. Game bindings use a hard-coded key map rather than parsing the
  active `controls.cfg`.
- Body-part wounds, bleeding rate, being eaten, imprisonment, location name,
  current tasks, trader money, occlusion, and distant world state are
  unavailable or unvalidated.
- Native identity still needs repeated validation across
  recruit/dismiss/reorder/KO/death, save/load, and zone transitions.
- Renderer stability remains open on this Intel Iris Xe host: later paused runs
  still failed after two clean soaks. The unproved lower-load path enforces
  30 fps, external-only 1080p, and rejection of recovered Windows Event 141s.
- The mock world tests orchestration, not Kenshi strategy competence.
