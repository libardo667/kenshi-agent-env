# ADR: Causal native commands

## Status

Accepted for P5. Portable tests, the pinned VS2010 SP1 Release x64 build, and
the supervised in-game protocol `0.3.0` rejection/acceptance/completion proof
pass.

## Problem

The first vendor bridge responded to a hotkey, chose the nearest matching role,
and exposed one mutable last-result field. A later run could therefore mistake
old state for a new command, while a changed selection, replaced target, or
newer observation could invalidate the planner's reason for acting.

Stable entity IDs solve reference identity but do not by themselves establish
who issued a command, which observation authorized it, or whether the exact
target accepted by the planner was the one Kenshi received.

## Decision

The Python runtime owns command identity and causality:

- every dispatch receives a globally unique `cmd-<32 lowercase hex>` ID and the
  complete `WorldStateRevision` used to authorize it;
- native vendor actions must name one exact stable target ID;
- `LiveEnvironment` rechecks fresh capability, `native_assisted` mode, current
  identity session, exactly one primary selection, and current safe vendor-role
  evidence;
- it atomically replaces `native_command.request.json` before sending the
  private bridge hotkey.

The plugin accepts only a strict versioned request whose command, telemetry
sequence, mode, identity session, selected stable ID, and target stable ID all
match issue-time state. It re-enumerates the bounded nearby set and never
chooses a substitute. Malformed or invalid requests are rejected; duplicate
command IDs are never reissued.

`native_control` retains at most 16 acknowledgements keyed by command ID.
Acknowledgements carry status, reason, request basis, exact target/selection,
and acknowledgement/acceptance/terminal telemetry sequences. One active
command is monitored on the game/UI thread:

- selection change cancels it;
- invalid/replaced target lifetime or lost vendor-role evidence cancels it;
- only dialogue bound to the exact target completes it.

Python ignores unrelated or old acknowledgements and waits only for its command
on a causally later snapshot. Definitive rejection performs no movement pulse.
Timeout, file/input failure, or uncertain delivery is not retried
automatically.

## Consequences

- The same caller-owned command identity now crosses ordinary single-step,
  continuous atomic action, stateful movement option, live request, native
  acknowledgement, receipt, log, replay, and metrics boundaries.
- The legacy last-command fields remain for diagnostics but cannot authorize
  progress.
- Exact revision equality can reject a request if telemetry advances between
  planning and the hotkey. That is intentional: a fresh plan gets a fresh
  command ID; stale intent is not silently rebased.
- The bridge remains one narrowly reviewed `PLAYER_TALK_TO` operation. This ADR
  does not authorize a generic native dispatcher, arbitrary game methods, live
  continuous mode, or automatic retry.
- Protocol `0.3.0` is additive at the telemetry level, but strict Python
  consumers must use the refreshed schemas.

## Evidence boundary

Portable tests cover strict models, atomic request publication before the
hotkey, caller context propagation, old-ack isolation, exact rejection without
movement, bounded lifecycle parsing, replay metrics, and source-level native
contract checks. The real pinned native project builds with Visual C++ 2010
SP1.

The supervised run installed DLL SHA-256
`9bbeea1826216365c5492ee94db4b692848a105fbb36bc794b02723e953a293b`.
A stale request was keyed and rejected without movement; a current request was
accepted for one exact selected/target pair, remained active while pathing, and
completed only when exact-target dialogue opened. Every boundary retained the
same command ID and basis. Kenshi was paused after each pulse and at completion,
then closed normally with no new plugin, renderer, or Application error. Exact
IDs, revisions, intervention, and rollback path are recorded in
`LIVE_VALIDATION_CHECKLIST.md`.
