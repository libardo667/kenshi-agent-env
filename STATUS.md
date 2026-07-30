# Implementation status

Current-state snapshot. Evidence lives in `git log` and `runs/<run-id>/`; the action surface in
generated [catalog](docs/generated/ACTION_CATALOG.md), game-derived [binding parity](docs/generated/GAME_BINDING_PARITY.md),
and the narrower [modeled-interface audit](docs/generated/MODELED_INTERFACE_AUDIT.md);
mutation coverage in the generated [attestation](docs/generated/MUTATION_ATTESTATION.md).

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
- Hosted calls project schemas to the current action surface, enforce system/static-prefix budgets,
  put stable prefixes first, and record provider cache diagnostics. Accepted planner output may
  carry one typed affordance candidate sidecar; cross-run aggregation remains non-authoritative.
- [Continuity](docs/ADR_CONTINUITY_EVIDENCE_CAPABILITIES.md) separates world evidence, run-local
  `ao-`/`po-` history, and durable memory; IDs resolve to immutable typed snapshots and an
  admissibility matrix before rendering; non-effects cannot become world proof or close a
  commitment. Rich windows sit over all-run compact digests returned only by elective read.
  Campaign-scoped schema 4 keeps structured provenance in append-only history and a rebuildable
  projection, with backed-up migration and read-only audit. Tiered recall reports omissions and
  receipts; `recall_memory` returns an identified, plan-bound one-call result without game input. A
  private fieldbook provides typed projects, evidence-bound entries, lifecycle, a selected summary,
  bounded elective reads, and disposable Markdown without becoming world authority. Lossless
  operator compaction fingerprints exact active sources, presents a read-only candidate, then
  revalidates and supersedes them atomically without deleting history. Retrieval is deterministic
  and logged; semantic rewriting and semantic MMR are unavailable.
- Hash-locked FCS starts install and launch with money/party proof. Mutation campaigns record source
  digests, so the generated ledger derives which committed results still apply; edits fail a gate.

## Live profiles

- `config/live.longform.yaml` — open-ended supervised `native_assisted`, continuous, unpaused
  profile with dialogue, memory, advisor, and acknowledgements. It is campaign-neutral;
  `./dev journey` requires an explicit `--campaign` or attested scenario for durable memory.
- `config/live.dialogue.yaml` — shorter stop-motion proof profile.
- `config/live.burnin.yaml` — legacy single-step calibrated profile; its continuous and
  food-procurement policies are retired.

From WSL, `./dev journey` preserves exact planner argv; `./dev recover` causally pauses an
interrupted native command, waits for its terminal acknowledgement, cleans exact owned windows,
and restores a stranded display; `./dev close` guards `WM_CLOSE`; `./dev crash` archives evidence.

Supervised runs have live-proven approach and dialogue activation, semantic startup, inventory and
trade navigation, one-step purchases with confirmed debits, readable world deltas, persistent
continuous memory, human-control handback, bounded native walking with camera follow, and exact
contextual operation through `context_task_started`. One bundled harvest retained an exact Iron
job at observed 5x speed, restored 1x speed, conserved three outputs into the selected actor, and
closed both inventories. Selling and equipping have portable coverage but no live proof.

## Native protocol (supervised live-loaded)

The plug-in hooks Kenshi-owned title and loaded-game update points and atomically replaces a
complete snapshot at roughly 2 Hz, covering world/squad state, window state, bounded visible
controls, reviewed mining resources, completeness markers, and a keyed acknowledgement ring. Field
semantics are in [`GUIDE_TELEMETRY_PROTOCOL.md`](docs/GUIDE_TELEMETRY_PROTOCOL.md).

The DLL is therefore not globally read-only. `interface_only` removes native command capabilities
before planning and rejects native actions again at the guard and environment boundaries.
`native_assisted` requires configuration opt-in plus a separate CLI acknowledgement.

## Open work

- No live plan has yet been retained in which the playing model itself authors `consult_advisor`
  and grounds its changed goal in both the attributed brief and current Kenshi evidence. One live
  call returned truncated invalid JSON; synthetic proofs do not satisfy the requirement.
- The full economic loop is closed live. `live-hub-survival-pair-20260729-r3` started on 20 cats
  with no food, harvested six Raw Iron, sold them for 612, and bought Bread, all from observed
  evidence. It stopped on an unactionable rejection, not on capability.
- FCS start `kae-01-broke-solo` is live-proven; no matrix run is fixture-attested.

## Known limitations

- Native build and supervised evidence are specific to the pinned Kenshi/RE_Kenshi/KenshiLib
  versions and this Windows host.
- Live proofs are single supervised runs. One harvest, one direction probe, and one Storm House
  exit do not generalize; raw `Character::isIndoors()` can retain a stale handle. The producer
  fails unresolved buildings closed while exit completion uses controller proof. No live run has
  exercised the continuity authority.
- Item cells expose base value, not the shop's charge: a trader applies its own multiplier and the
  asking price is never exported. `live-hub-survival-pair-20260729-r3` declared `expected_price` 300
  for Bread and was charged 549. `max_purchase_price` and `min_money_after_purchase` are enforced
  against the declared price, so a spending cap is advisory rather than binding. The tooltip price
  check exists but sits behind `tooltip_visible`, which was false for that entire run.
- A causally later observation stops stale pre-action state from satisfying a postcondition.
  Mechanical effects are controller-terminal or derived from the immediate dispatch baseline;
  ambiguous UI effects remain planner-authored and can still confuse a correlated later change
  with the intended effect.
- Synthetic portable and replay evidence proves campaign-scoped continuity across real process
  restarts, including exact-identity exclusion, bounded fieldbook reopening, current-telemetry
  precedence, rejection correction, and evidence-backed commitment closure. No supervised live
  restart has exercised that authority.
- Body-part wounds, bleeding rate, being eaten, imprisonment, task stacks, trader
  money, occlusion, and distant world state are unavailable or unvalidated; source-scan capacity
  makes absence unknown.
- Native identity needs validation across recruit/dismiss/reorder/KO/death, save/load, and zones;
  entity memories never cross a session identity.
- Game bindings use a hard-coded key map, not the active `controls.cfg`. Renderer stability remains
  open: the 30 fps external-only path rejects Event 141s but has no long soak or competence proof.
