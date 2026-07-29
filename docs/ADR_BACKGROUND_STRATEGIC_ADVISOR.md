# ADR: Background strategic advisor

Status: Accepted
Date: 2026-07-29
Supersedes: decision item 2 of `ADR_STRATEGIC_ADVISOR.md`

## Context

The original advisor contract made a consultation the only step in its plan. That preserved one physical
action authority, but it also paused ordinary play for a read-only hosted request. Strategy latency should
not stop a livestream when safe, reversible work remains available.

## Decision

`consult_advisor` queues one single-flight background request and returns a typed pending receipt with zero
controller primitives and no world command. A plan may contain independent foreground steps after the
consultation. Later observations expose `request_pending`; they suppress duplicate consultation while
allowing safe play to continue. The completed source-attributed brief enters a later planner observation,
where the playing model alone decides how to ground it in current world evidence.

Cancellation and run cleanup own the background task. Provider failure, malformed output, or cancellation
cannot silently outlive the run or acquire controller authority.

## Consequences

- Hosted strategy latency no longer owns game pause or blocks independent foreground work.
- Advice may be stale when it returns, so it remains knowledge rather than action authority.
- The playing model may do low-cost exploratory work while waiting, but its quality is evaluated separately
  from the concurrency mechanism.

## Evidence boundary

Portable tests cover single-flight queuing, later brief delivery, cleanup cancellation, and exact
continuation of truncated hosted output. Supervised run `live-advisor-background-20260729-r5` queued two
requests with zero controller primitives; foreground movement continued while each was pending, and both
briefs reached later planner observations. The run does not prove malformed-EOF recovery in live play.
