# ADR: affordance demand is an accepted-output sidecar

Status: accepted 2026-07-29; supersedes the planner-action mechanism in
`ADR_RUNTIME_AFFORDANCE_REQUESTS`.

## Decision

Planner outputs may carry one typed `affordance_candidates` entry alongside a
useful decision, plan, or patch. The runtime records it automatically only after
that output passes its normal acceptance boundary. It consumes no action,
creates no world command, and grants no planner authority.

Candidates describe grounded intentions with no safe advertised route. Stale
references, safety refusals, ambiguous evidence, and failures of existing
routes are not classified as missing affordances. Rejected outputs contribute
no candidate. Every retained or duplicate candidate remains
`needs_engineering_review` and uses the established typed cross-run aggregation
key.

The former `request_affordance` action remains in the broad protocol union for
old logs and the direct compatibility handler, but is removed from planner
action unions. Promotion still requires an engineer-owned binding, safety
contract, and causal terminal.
