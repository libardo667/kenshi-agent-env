# ADR: completed settlement travel uses its owned endpoint

Status: accepted (2026-07-30)

Supersedes the gated completion proof in
[`ADR_SETTLEMENT_ENTRY.md`](ADR_SETTLEMENT_ENTRY.md). Its two-leg ownership,
exact identity, and fail-closed cancellation decisions remain in force.

## Decision

A gated map destination never completes at its direction-dependent exterior
waypoint. Reaching that waypoint issues one controller-owned interior movement
order to the town's native-resolved position.

After that exact interior leg reaches its endpoint, exact current-town identity
proves arrival. Selected-character inside-walls state may corroborate entry but
is not mandatory: valid town geometry does not always expose an inside. An
identity mismatch still cancels rather than inferring arrival from distance,
names, nearby characters, or a stopped path.

Issue-time already-reached rejection remains stricter. Exact town identity alone
does not suppress a new gated-town command before the controller has owned and
completed the interior leg.
