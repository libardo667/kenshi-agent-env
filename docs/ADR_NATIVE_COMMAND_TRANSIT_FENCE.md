# ADR: native command freshness includes bounded transit

Status: accepted, 2026-07-28

## Context

The Python input boundary revalidates the latest canonical observation inside
the polite lease. Immediately before native dispatch it reads telemetry again,
rebinds the exact action, atomically writes a request, and sends the reviewed
hotkey. The plug-in then reads that file from Kenshi's UI-thread hook.

The plug-in formerly required the request's telemetry sequence to equal its
current publication sequence. That treated cross-process delivery time as
authority drift. A live production request was issued from sequence 234 and
rejected at sequence 236 even though the selected character, target, role, UI,
session identity, and action authority were unchanged.

## Decision

A native request basis may be current or at most four telemetry publications
behind the UI-thread sequence. A future basis or a basis five or more
publications old remains invalid. With the fixed 500 ms publication interval,
the allowance bounds normal file, input, and hook transit to two seconds.

This window does not substitute old facts for current facts. After it passes,
the plug-in still re-resolves the identity session, exact selected character,
target lifetime and role, UI conflicts, and every command-specific authority
condition before issuing an order. Python still rebases only forward and
re-proves its own contract against the newest telemetry immediately before
writing the request.

## Consequences

- Ordinary transport delay no longer makes native acceptance a timing coin
  flip.
- A stranded request cannot survive indefinitely or cross a session identity.
- The acknowledgement retains both the request basis and later native
  acknowledgement sequence, so the transit lag remains inspectable.
- The native conformance executable owns the edge invariant: lags zero through
  four pass, lag five and future bases fail.
