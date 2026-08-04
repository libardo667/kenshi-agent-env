# Stage 8 reconstruction acceptance

Accepted 2026-08-04 on the commit tagged
`reconstruction-stage-8-accepted`.

This report closes the architectural reconstruction described by
`docs/ARCHITECTURE_RECONSTRUCTION.md`. The live records below are supervised,
bounded evidence for particular control paths. They do not establish general
Kenshi competence, and the synthetic restart record does not establish live
control.

## Final shape

The final structural gate parses the production tree and proves that the named
legacy owners and compatibility modules are absent. It also proves exactly one
definition of each of these owners:

- planner-visible selection: `AffordanceSelection`;
- private operation registry: `OPERATION_DEFINITION_LIST` and its one derived
  `OPERATION_DEFINITIONS` mapping;
- fresh executable binding: `OperationBindingAuthority`;
- cross-cutting authorization: `OperationAuthority`;
- execution routing: `HandlerRegistry` and `ExecutionKernel`;
- scheduling: `RunCoordinator`;
- planner payload: `PlannerContextAssembler`;
- outcome evidence: `OutcomeRecorder`;
- durable continuity: `ContinuityService`;
- final safety: `ensure_final_safe_state`;
- independent supervision: `SafetySupervisor`.

Mock, replay, and live remain narrow `AgentEnvironment` adapters. The base
environment has no `step()` or dispatch method, and the live adapter neither
imports semantic action classes nor switches on operation kinds. The production
import graph has zero strongly connected components. Core types import no
application, adapter, CLI, or tooling layer, and production imports do not point
outward into the tooling perimeter.

## Deleted structure

Relative to `reconstruction-stage-0-baseline`, Git records 27 outright file
deletions:

- Config and generated residue: `config/calibration.example.yaml`,
  `docs/generated/MUTATION_ATTESTATION.md`,
  `docs/generated/OBSERVED_BLOCKERS.md`, and
  `docs/generated/OPERATION_QUEUE.md`.
- Obsolete probes and policy examples: the nine files under `examples/` named
  `procurement_*_probe.jsonl` or `scripted_policy.jsonl`.
- Ledger exporters: `scripts/export_blocker_ledger.py` and
  `scripts/export_mutation_ledger.py`.
- Production owners: `action_completeness.py`, `blocker_ledger.py`,
  `models.py`, `mutation_ledger.py`, `runtime_context_menu.py`, and the two
  Python files under `skills/`.
- Tests for deleted owners: `test_action_completeness.py`,
  `test_blocker_ledger.py`, `test_mutation_ledger.py`,
  `test_mutation_visibility.py`, and `test_skill_registry.py`.

The major deleted symbols and entry points include `ActionContract`,
`ACTION_CONTRACTS`, universal `ReferenceBinding`, `AgentEnvironment.step`,
`ContinuousPlanExecutor._execute_step`, `LiveEnvironment._execute_live`,
`AgentRuntime._run_single_step`, `AgentRuntime._run_continuous`, `ActionGuard`,
`SkillAction`, and `MacroRegistry`. The 5,417-line universal `models.py` and its
compatibility import path are gone; `core/__init__.py` is not a replacement
barrel.

The source/test delta, including the final acceptance test, is:

| Scope | Added | Deleted | Net |
| --- | ---: | ---: | ---: |
| `src/` | 23,784 | 22,846 | +938 |
| `tests/` | 4,735 | 8,391 | -3,656 |
| Combined | 28,519 | 31,237 | -2,718 |

These figures count moves as Git line changes where similarity detection does
not preserve a rename. Tooling was moved to `kenshi_agent.tooling`; it was not
silently deleted.

## Authority replacements

| Former authority | Surviving owner |
| --- | --- |
| `ActionContract` registry and completion lookup | `OPERATION_DEFINITION_LIST`, typed operation definitions, and runtime conditions |
| Environment and executor semantic switches | `HandlerRegistry`, `ExecutionKernel`, family handlers, and narrow mechanics ports |
| Separate single-step and continuous loops | One `RunCoordinator` with scheduling policy |
| `ActionGuard` plus macro-specific policy | `OperationAuthority`, pure `OperationPolicy`, and `ActionBudgetLedger` |
| Handler-local executable rebinding | `OperationBindingAuthority` |
| Observation/runtime planner-payload assembly | `PlannerContextAssembler` |
| Distributed terminal assessment and logging | `OutcomeRecorder` |
| Runtime-owned memory/fieldbook callbacks | `ContinuityService` |
| Mixed `models.py` vocabulary and lazy imports | Direct bounded-context modules under `core/` |
| CLI as a second composition center | `application.py`, entered by thin CLI and live-development adapters |

The reconstruction removed the package-cycle pressure created by the universal
model barrel and its lazy higher-layer imports. The final AST import gate
requires zero production cycles, direct defining-module imports, an empty core
barrel, and one-way tooling dependencies.

## Compatibility removed

The generic macro/skill execution mode and its name-matched validation path are
deleted. Canonical live configuration no longer accepts the retired fields
`allow_skills`, `calibrated_macro_set_hash`, `macros`,
`native_approach_skill`, `pause_skill`, `unpause_skill`,
`semantic_pointer_skills`, `stateful_approach_skills`,
`stateful_movement_skills`, `stop_when_terminated`, `crop_client_area`, or
`require_cli_execute_flag`. Python retains no legacy executor, migration
fallback, compatibility re-export module, blocker queue, or mutation ledger.

The unchanged native protocol may still parse its optional historical
calibration identity field at the external boundary. That is protocol
compatibility in the adapter, not a surviving Python macro owner.

## Verification evidence

### Portable

The final candidate passes the complete Python suite, Ruff, strict mypy over
`src`, deterministic schema export and staleness checks, deterministic document
export and staleness checks, and the Stage 8 absence/single-owner gates. The
suite covers mock single-cycle and continuous schedules through the same
coordinator, representative replay traces, restart continuity, human handoff,
F12 supervision, final safety, import direction, and the acyclic package graph.

The restart evidence ID is
`reconstruction-stage-0-restart-continuity` (synthetic portable evidence).

### Native

`scripts/build_native.ps1` completed a Release x64 build with 0 errors. The
fresh `NativeCommandProtocolTests.exe` passed every shared JSON fixture and
semantic check. Native source has no diff from the Stage 0 tag.

- Telemetry protocol: `1.12.0`.
- Native command schema: `1.2`.
- Installed live-proven DLL: 286,720 bytes, SHA-256
  `e93d7278fc81c04f562e607322af7f805027fd2523e276bc4ca463e0f54cde77`.
- Fresh Release build: 286,720 bytes, SHA-256
  `062651920ae24347a1761a86876bdd8b741da4446f0ac3d1ab24e3f8aa317d98`.

The two binaries differ at 13 bytes: PE link timestamp, checksum, debug
timestamp, and PDB age metadata. Their RSDS signature is identical, and the
source tree is unchanged. No unproved DLL was installed for acceptance.

The current installed artifact produced fresh telemetry during
`stage7-tooling-live-proof-20260804c`: sequences 886 through 910 retained one
stable `identity_session_id`, reported non-stale observations, ended paused,
and left no controller-owned inventory, dialogue, management, or modal window
open. The later supported `recover` and `stop` commands also passed.

### Supervised live

The final matrix is covered by these durable run IDs:

- `live-one-choice-group-movement-soak-20260803-r1`: current affordance offer,
  exact stable-identity rebind, and native whole-party regroup terminal.
- `live-hub-survival-pair-20260729-r3`: conserved harvest of 5 Raw Iron, sale of
  4 Raw Iron (money 224 to 632), purchase of 1 Bread (money 632 to 83), and
  causal final pause at telemetry sequences 26947 to 26949.
- `reconstruction-stage-3-run-coordinator-r3`: 11 accepted plans through the
  physical coordinator, including monitored native movement and replanning.
- `reconstruction-stage-4-control-boundary-r5`: live human-input preemption,
  exact cancellation, confirmed pause, visible handoff countdown, automatic
  takeover, F12 disarm, and a second confirmed pause.
- `stage7-tooling-live-proof-20260804c`: canonical public launch/run path,
  exact active-interface transition, typed operation completion, and clean
  final pause at sequence 910; followed by supported recover and stop.
- `live-party-control-set-aware-soak-20260803-r1`: 47 strategic planner calls,
  47 accepted plans, 52 succeeded steps, 53 recorded action outcomes, and 60
  affordance receipts.

The moderate soak also contains eight failed steps and no `run_finished_safety`
event. It is evidence of sustained scheduling and execution, not a clean-final
state proof; the independent Stage 7 run/recover/stop evidence supplies that
proof. The distilled Stage 0 hashes preserve the first three baseline records,
and every named local event bundle was re-hashed before acceptance.

## Deferred limitations

These are gameplay or perception limitations, not surviving architecture debt:

1. Prospecting resource scalars measure area coverage, not deposit counts, and
   the agent cannot yet read the spatial deposit panel.
2. A stalled monitored approach can consume minutes before its wall-clock
   timeout; a progress-based stall terminal is future gameplay work.
3. Run bundles record the chosen affordance but not the complete offered and
   withheld menu, which makes negative offer post-mortems expensive.
4. Harvest authorability requires singleton selection earlier than the exact
   actor/conservation invariant needs it; phase-scoped competence is future
   work.

None of these expands the reconstruction scope. They are the next frontier only
after this accepted architecture is merged.
