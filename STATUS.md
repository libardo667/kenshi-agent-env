# Implementation status

Current-state snapshot. Evidence lives in `git log` and `runs/<run-id>/`; the action surface lives
in generated [catalog](docs/generated/ACTION_CATALOG.md) and
[coverage](docs/generated/UI_AFFORDANCE_COVERAGE.md).

## What works

- A deterministic Kenshi-like mock environment with strict Pydantic models, JSONL lifecycle logs,
  optional full-observation replay, SQLite memory, and
  heuristic/scripted/subprocess/OpenAI/OpenRouter planners.
- `single_step` is default; feature-flagged `continuous` accepts bounded plans, future patches,
  and guarded interruption. Both transactionally reserve global rate/purchase authority;
  continuous also owns plan risk budgets. See [bounded continuous
  planning](docs/ADR_CONTINUOUS_PLANNING.md).
- One authoritative observation pump and bounded `WorldStateStore` preserving revisions, deltas,
  events, entity lifetimes, plan/command state, and queues.
- An independent deterministic supervisor preempts on stale or stalled telemetry, capability loss,
  threats, human input, F12, or unauthorized unpause. Cleanup succeeds only after a later
  observation confirms the safe state.
- One idempotent final-state owner covers completion, stop, budget, failure, cancellation, and
  exception exits. Executed live runs fail distinctly unless fresh telemetry confirms pause;
  cleanup input needs later confirmation.
- A final in-lease authorization fence revalidates action/reference safety, plan conditions,
  control mode, calibration, input ownership, and freshness just before input, in both schedulers.
  Unfresh authority emits zero primitives.
- Human input cancels the active plan and hands control over visibly. The long-form profile may
  restore a running world after a quiet resettable countdown; F12 disarms automatic takeover for
  the run.
- A read-only guide-grounded strategic advisor. `consult_advisor` spends a strategic turn, creates
  no world command, emits zero primitives, fails closed on unknown source IDs, and suppresses
  unchanged-state requests pre-call.
- `request_affordance` records typed, non-authoritative demand; aggregation splits raw reruns from
  fixture-attested recurrence across five matrix axes.
- [Continuity](docs/ADR_CONTINUITY_EVIDENCE_CAPABILITIES.md) separates world evidence, run-local
  `ao-`/`po-` history, and durable memory; IDs resolve to immutable typed snapshots and an
  admissibility matrix before rendering; non-effects cannot become world proof or close a
  commitment. Rich windows sit over all-run compact digests returned only by elective read.
  Campaign-scoped schema 4 transactionally keeps structured lifecycle provenance in append-only
  history and a rebuildable projection, with backed-up migration and read-only audit. Tiered recall
  reports omissions and receipts; `recall_memory` returns an identified, plan-bound
  completed/unavailable/failed receipt for exactly the next call, without game input. A private
  campaign fieldbook provides typed named projects, evidence-bound entries, lifecycle, one
  selected summary, bounded elective reads, and disposable Markdown without becoming world authority.
- Hash-locked FCS starts install and launch with money/party proof. The mutation campaign is
  permanently bounded to nine named authority modules and records strict evidence per run.

## Live profiles

- `config/live.longform.yaml` — open-ended supervised `native_assisted`, continuous, unpaused
  profile with dialogue, memory, advisor, and acknowledgements. It is campaign-neutral;
  `./dev journey` requires an explicit `--campaign` or attested scenario for durable memory.
- `config/live.dialogue.yaml` — shorter stop-motion proof profile.
- `config/live.burnin.yaml` — legacy single-step calibrated profile; its continuous and
  food-procurement policies are retired.

From WSL, `./dev journey` preserves exact planner argv; `./dev close` confirms pause then guards
`WM_CLOSE`; `./dev crash` archives evidence before bounded dismissal. Supervised runs have shown
generic approach and dialogue activation, semantic startup, inventory and trade navigation,
one-step purchases with confirmed debits, readable world deltas, persistent continuous memory, and
human-control handback. One run planned during native options in an unpaused world and ended
safely paused, but proved no income or task persistence. Selling and equipping have portable
coverage but no live proof. Native walking supports exact characters, bounded direction, and
no-argument exits; a shared ten-second no-progress terminal prevents poisoning later movement, and
camera follow has one obstructed-move proof. Exact contextual operation is live-proven on one
Copper Resource via `context_task_started`, the visible `Operating machine` goal, and a safe final
pause. A fresh zero-ore run reached Raw Iron output and opened the source, but four source-only
transfers changed neither inventory; collection needs the separately open destination and its
conservation proof is absent.

## Native protocol 1.1.0 (supervised live-loaded)
The plug-in hooks Kenshi-owned title and loaded-game update points and atomically replaces a
complete snapshot at roughly 2 Hz. Telemetry covers pause, speed, money, game time, camera
position and bearings; stable session-scoped squad, selection, nearby-character, dialogue-target,
world-target and command identities; squad life/consciousness/combat state, position, resolved
indoor membership, nutrition reserve, blood, UI-facing current goal, and bounded inventory;
dialogue, trade, inventory, stats and management window state; up to 224 visible controls with
window ownership and normalized bounds; nearest-first structurally reviewed mining resources from
bounded local and outer queries; completeness markers for bounded squad/control inventories; exact
contextual-inventory ownership; and a keyed acknowledgement ring for seven declared commands. Its
production and inventory-opening path has live evidence; matching later destination quantity does
not prove conserved transfer.

The DLL is therefore not globally read-only. `interface_only` removes native command capabilities
before planning and rejects native actions again at the guard and environment boundaries.
`native_assisted` requires configuration opt-in plus a separate CLI acknowledgement. Wire
semantics are in [`GUIDE_TELEMETRY_PROTOCOL.md`](docs/GUIDE_TELEMETRY_PROTOCOL.md).

## Open work

- No live plan has yet been retained in which the playing model itself authors `consult_advisor`
  and grounds its changed goal in both the attributed brief and current Kenshi evidence. One live
  call returned truncated invalid JSON; synthetic proofs do not satisfy the requirement.
- Conserved output transfer has portable tests and native conformance; production and source
  opening are live-proven, but a fresh follow-up stopped on repeated non-causal destination plans.
  Collection and income are not proven.
- FCS start `kae-01-broke-solo` is live-proven; no matrix run is fixture-attested.

## Known limitations

- Native build and supervised evidence are specific to the pinned Kenshi/RE_Kenshi/KenshiLib
  versions and this Windows host.
- Live proofs are single supervised runs. One direction probe and one Storm House exit do not
  generalize; raw `Character::isIndoors()` can retain a stale handle. The producer fails
  unresolved buildings closed while exit completion uses controller proof. Chosen remote map
  travel has no semantic action, and no live run has exercised the continuity authority.
- Item cells expose base value, not the final shop charge; the real debit is confirmed only after
  the purchase, from later money telemetry.
- A causally later observation stops stale pre-action state from satisfying a postcondition, but
  most success conditions are planner-authored. Only `controller_verified` contracts carry effect
  proof, so later correlated state can still be mistaken for the intended effect.
- Continuity now has an explicit evidence-capability matrix and structured provenance nodes; a
  long-horizon restart proof remains open.
- Body-part wounds, bleeding rate, being eaten, imprisonment, location name, task stacks, trader
  money, occlusion, and distant world state are unavailable or unvalidated; source-scan capacity
  makes absence unknown. Fifty-six mutation shards remain unattested.
- Native identity needs validation across recruit/dismiss/reorder/KO/death, save/load, and zones;
  entity memories never cross a session identity.
- Game bindings use a hard-coded key map, not the active `controls.cfg`. Renderer stability remains
  open: the 30 fps external-only path rejects Event 141s but has no long soak or competence proof.
