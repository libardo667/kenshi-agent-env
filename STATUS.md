# Implementation status

Current-state snapshot. Evidence lives in `git log` and `runs/<run-id>/`; the action surface lives in generated [catalog](docs/generated/ACTION_CATALOG.md) and [coverage](docs/generated/UI_AFFORDANCE_COVERAGE.md).

## What works

- A deterministic Kenshi-like mock environment with strict Pydantic models, JSONL
  lifecycle logs, optional full-observation replay, SQLite memory, and
  heuristic/scripted/subprocess/OpenAI/OpenRouter planners.
- `single_step` is the default scheduler. Feature-flagged `continuous` planning
  accepts bounded typed plans and future patches plus exact opt-in movement
  interruption, owns action and risk budgets, and rechecks typed conditions
  before every step. See [bounded continuous planning](docs/ADR_CONTINUOUS_PLANNING.md).
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
- A final in-lease authorization fence covers ordinary planner-authored actions
  in both schedulers: just before the first primitive, current state, action and
  reference safety, plan conditions, control mode, calibration, human input, and
  emergency-stop evidence are revalidated. Lost authority emits zero input.
- Human input cancels the active plan and hands control over visibly. The
  long-form profile may restore a running world after a quiet, resettable
  takeover countdown; F12 disarms automatic takeover for the run.
- A read-only guide-grounded strategic advisor. `consult_advisor` consumes a
  strategic turn, creates no world command, and emits zero primitives. Unknown
  source IDs fail closed; unchanged-state requests are suppressed before the call.
- `request_affordance` records a typed intent class plus Kenshi capability slug,
  evidence, and urgency without granting authority or input. Aggregation separates
  raw reruns from fixture-attested save/scenario recurrence across five matrix axes.
- Hash-locked FCS Game Starts install without overwrite and launch through exact
  controls with money/party proof; closed saves become fixture-attested.
- Persistent memory unions bounded general recall with exact, fresh
  current-target matches and exposes dialogue-approach repetition by target.

## Live profiles

- `config/live.longform.yaml` — open-ended supervised `native_assisted`,
  continuous, unpaused profile with dialogue, memory, advisor, and acknowledgements.
- `config/live.dialogue.yaml` — shorter stop-motion proof profile.
- `config/live.burnin.yaml` — legacy single-step calibrated profile; its
  continuous and former food-procurement policies are retired.

From WSL, `./dev journey` preserves exact planner argv; `./dev close` confirms pause
then guards `WM_CLOSE`; `./dev crash` archives evidence before bounded dismissal.
Supervised runs have demonstrated generic approach and dialogue activation,
semantic startup, inventory and trade navigation, one-step purchases with
confirmed debits, readable world deltas, persistent continuous memory, and
human-control handback. One run planned during native options in an unpaused
world and ended safely paused, but proved no income or task persistence. Selling
and equipping have guarded portable coverage but no live proof. Native walking
supports exact characters, bounded direction, and no-argument exits; a shared
ten-second no-progress terminal prevents poisoning later movement. Native camera
follow has one obstructed-move proof. Exact contextual operation is live-proven on
one Copper Resource through `context_task_started`, the visible `Operating machine`
goal, and a safe final pause. Retained production reached output and opened its inventory.

## Native protocol 1.1.0 (supervised live-loaded)
The plug-in hooks Kenshi-owned title and loaded-game update points and atomically
replaces a complete snapshot at roughly 2 Hz. Telemetry covers pause, speed,
money, game time, camera position and bearings; stable session-scoped squad,
selection, nearby-character, dialogue-target, world-target and command
identities; squad life/consciousness/combat state, position, resolved indoor membership,
nutrition reserve, blood, UI-facing current goal, and bounded inventory; dialogue, trade, inventory,
stats and management window state; up to 224 visible controls with window
ownership and normalized bounds; nearest-first structurally reviewed mining
resources combined from bounded local and outer queries; completeness markers
for bounded squad/control inventories; exact contextual-inventory ownership; and
a keyed acknowledgement ring for seven declared commands. Its production and
inventory-opening path has supervised live evidence; conserved transfer does not.

The DLL is therefore not globally read-only. `interface_only` removes native
command capabilities before planning and rejects native actions again at the
guard and environment boundaries. `native_assisted` requires configuration
opt-in plus a separate CLI acknowledgement. Wire semantics are in
[`GUIDE_TELEMETRY_PROTOCOL.md`](docs/GUIDE_TELEMETRY_PROTOCOL.md).

## Open work

- No live plan has yet been retained in which the playing model itself authors
  `consult_advisor` and its changed goal is grounded in both the attributed
  brief and current Kenshi evidence. One live call returned truncated invalid
  JSON; synthetic proofs do not satisfy the requirement.
- Conserved output transfer has portable tests and a native conformance build,
  but no supervised Kenshi proof. Production and inventory opening are proven;
  collection or income claims still require equal source loss and inventory gain.
- FCS start `kae-01-broke-solo` is live-proven; no matrix run is fixture-attested.

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
- Game bindings use a hard-coded key map rather than parsing the active
  `controls.cfg`.
- Body-part wounds, bleeding rate, being eaten, imprisonment, location name,
  internal task stacks, trader money, occlusion, and distant world state are
  unavailable or unvalidated; source-scan capacity still makes absence unknown.
- Native identity still needs repeated validation across
  recruit/dismiss/reorder/KO/death, save/load, and zone transitions.
- Exact entity memories deliberately do not transfer by display name across a
  native process or game-session identity change.
- Renderer stability remains open on this Intel Iris Xe host: the 30 fps
  external-only path passed startup and rejects Event 141s, but has no long soak.
- The mock world tests orchestration, not Kenshi strategy competence.
