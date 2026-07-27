# Report: input-boundary mutation campaign

Write-once. Supersede with a later dated report.

## Question

Would the suite reject incorrect final authority decisions or incomplete
evidence after an input lease wait?

## Invalid first run

The first campaign generated no executable mutants. This was not a clean
result: the installed `mutmut` deliberately skips decorated classes, and all
`ExecutionToken` behavior lived inside `@dataclass`.

The frozen, slotted field contract now lives in a data-only base while the
public token's executable methods live in an undecorated subclass. The reports
property uses a mutation-visible getter. Focused tests confirm the existing API,
immutability, dispatch behavior, and type contract remain intact.

## Valid baseline

The first executable campaign generated 179 mutants: 115 killed and 64
survivors. Half of the survivors changed human-readable reason prose only. The
other half exposed missing evidence-contract assertions:

- every observation-bound result must retain the exact boundary revision;
- plan ID, plan version, step ID, validated revision, and lease wait must survive
  every acceptance and rejection route;
- a condition rejection must retain all evaluations in contract order; and
- telemetry at the exact configured age ceiling remains authorized.

The new invariants killed all 32 behavioral and evidence-propagation survivors.
They cover stale or unknown telemetry, unknown ceiling, over-age input,
revision regression, control-mode drift, human authority withdrawal, action
revalidation, false conditions, and successful revalidation through one common
report contract.

## Result

Static reason fragments are excluded as diagnostic-only; no predicate,
authority value, revision, evaluation, identifier, or decision is excluded.
The strict campaign killed all 140 generated mutants with zero survivors or
untested mutants.

This attends only `input_boundary`. It also establishes a rollout rule: a shard
whose behavior is hidden by a decorator must be made mutation-visible before it
can count toward project completion.
