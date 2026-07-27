# External review — 2026-07-27

Write-once. Supersede by adding a later `REPORT_<date>_*.md`; never edit this
file. Each finding names the invariant that would close it, so the tests outlive
this snapshot.

Scope: external read of the public checkout at `9c012bd`. Portable evidence only
— `pytest` (586 passed, 1 skipped), `ruff`, `mypy src` (63 files), `export_docs`
byte-comparison, mock `single_step` and `continuous` runs. No Windows, native, or
live evidence was produced or is claimed.

## 1. Memory recall cannot surface a learned negative constraint

`memory.py:74` orders recall by `salience DESC, last_accessed_at DESC, id DESC`
and nothing else. The `query` parameter exists, but the sole production caller
(`runtime.py:1984`) never passes it — only tests do. Salience is model-authored
and `memory.py:47` upserts it as `MAX(existing, incoming)`, so it ratchets up and
never decays. `active` is written `1` at `memory.py:50` and never set to `0`
anywhere. Nothing associates a memory with an entity, so when a target reappears
in `nearby_entities` no mechanism retrieves what was learned about it.

Why it matters: the 80-turn advisor run recorded 13 of 20 dialogue approaches
against one target, reopening a branch the planner had already written off as
unaffordable. That is the correct conclusion being lost, not absent — a retrieval
failure. The prescribed fix supplies strategy knowledge the model demonstrably
already had.

Failing invariant: when later writes exceed `max_recalled_memories`, a memory
bound to an entity present in the current observation still appears in
`observation.memories`.

Cheaper than the advisor, and a clean ablation against it: tag writes with
`target_id`, union top-N-by-salience with entity-scoped recall, hold everything
else fixed, and measure approaches-per-target.

## 2. `single_step` live dispatch carries no `ExecutionToken`

`continuous_executor.py:881` passes `token=token`. The three `runtime.py`
dispatch sites (366, 1350, 1519) do not, and `env/live.py` guards the whole
boundary with `if token is not None`.

So in `single_step` live none of the generic fence runs: no calibration recheck,
no revision-regression check, no control-mode-change check, no re-check of
`human_input_detected` or `emergency_stop_detected` after the unbounded polite
lease wait. What remains is per-action rebinding, which five contracts perform
and the rest do not; native contracts are covered differently, through
revision-matched request identity. `STATUS.md` describes the fence under "What
works" without naming a mode, which reads as universal.

Failing invariant: a `single_step` live dispatch whose authorizing observation
became invalid during the lease wait emits zero primitives.

## 3. The input boundary rejects revision regression but not stall

`input_boundary.py:136` is the only revision check, and it is
`validated_revision.is_later_than(boundary_revision)` — an identical revision
passes. `observation.telemetry_stale` is never consulted in `_decide()`.

The 10 Hz pump samples a ~2 Hz producer, so identical-revision is the common
case rather than an edge case. Combined with an explicitly unbounded lease wait,
the boundary can revalidate on evidence of arbitrary age provided nothing
regressed. The per-action `_rebind_in_lease` calls do a fresh stale-checked read,
so the sharp edges are covered — which means the generic fence is doing less than
its docstring claims, and the protection is per-action rather than structural.

Failing invariant: a boundary revalidation on stale telemetry, or on an
observation older than a configured ceiling, rejects.

## 4. Rate and purchase budgets are spent at validation (low severity)

`safety.py:54` and `:139` call `_consume_rate_budget` inside `validate()`;
`:56` and `:145` increment `_purchase_count` there. An action rejected at the
input boundary emits zero primitives but has already spent both.

Fails safe, so severity is low. It does mean `max_actions_per_minute` throttles
proposals rather than input, and that the safety counters and the receipt
counters are not reconcilable.

Failing invariant: budgets move on execution evidence — or the config fields are
renamed to say what they actually limit.

## 5. `LOOP_PROMPT.md` is the only uncapped prose in the repo

`tests/test_docs_hygiene.py:38` lists four root documents; `LOOP_PROMPT.md` is
not among them, so `_capped_docs()` never sees it. At 415 lines it exceeds those
four combined, and it is the file that steers the loop. `CHANGELOG.md` is exempt
too — but with its reason stated in code, which is the difference between an
exemption and an oversight.

Failing invariant: every root prose file is either capped or carries an in-code
reason for its exemption.

## 6. The cross-layer evidence axis was deleted, not migrated

The removed `DOCUMENTATION_TRUTH.md` carried two ladders. `LOOP_PROMPT.md` §11
preserves the runtime-strength one. The consistency one — declared → advertised →
serialized → accepted end to end — has no successor.

Commit `8993b10` is that exact failure: 0.8.1 telemetry reported `indoors: true`
while native rejected the exit as `not_indoors`. Declared and serialized, never
accepted end to end. The current vocabulary has no name for that state, so the
next instance has no label to be reported under.

Failing invariant: every capability a contract requires is produced by both the
mock environment and the native fixture corpus — checked, not asserted in prose.

## Not investigated

Windows, native build, and live behaviour. The C++ beyond structure. The advisor
corpus and its grounding. `world_state.py` revision semantics. Overlay and
display lease. Whether any test would fail if the behaviour it names broke — the
mutation-testing item already in the loop queue is the honest answer to that, and
it is the only proposed gate here that is not self-referential.
