# Report: planning mutation campaign

Write-once. Supersede with a later dated report.

## Question

Would the planning tests reject incorrect condition evaluation, plan authority,
risk accounting, future-plan patching, and interruption handoff?

## Baseline

The first attended campaign generated 714 mutants: 331 killed, 16 with no
associated test, and 367 survivors. The largest gaps were field resolution,
condition evaluation, outer plan validation, and future-patch validation.
Selected-character resolution, revision freshness, telemetry availability,
elapsed-time conversion, risk conservation, and interruption context also had
surviving changes.

## Repaired invariants

The suite now proves every declared field path against a uniquely valued
observation and checks that the tested path set exactly equals the schema enum.
It covers selected-character precedence, operator and scalar-type truth tables,
capability aliases, telemetry age and revision boundaries, live-policy argument
forwarding, configured budget equality, and causal-channel requirements.

Risk tests conserve pointer, purchase, and native-action counts across complete
plans and budget-ledger consumption. Future patches must preserve plan identity,
version, current basis, protected prefixes, and remaining budgets. Interruption
handoff requires the complete active context and every prior fact.

Mutation exposed two redundant implementation paths. Risk totals no longer
multiply retry counts because every currently retryable action has zero risk and
risky retries already invalidate the plan. A duplicate non-positive action
budget check was removed because model validation fails closed before planning.
Only diagnostic wording and schema-proven unreachable assertions are excluded;
no authority branch or decision value is excluded.

## Result

The strict final campaign killed all 598 generated mutants with zero survivors
or untested mutants. The ordinary behavioral, lint, and configured source-type
gates also passed. This result attends only `planning`; it does not establish
project-wide mutation adequacy.
