# ADR: Stable native identity

## Status

Accepted for the P5 identity boundary. Causal native command envelopes remain
separate unfinished P5 work.

## Problem

The plugin labeled squad and nearby characters by list position. Reordering a
list could therefore rename an entity, while two characters with the same
display name could not safely anchor a target. Raw pointers would create a
different lifetime and privacy problem.

## Decision

Protocol `0.2.0` adds `identity.stable_handles` and
`identity_session_id`. Entity IDs are derived from a validated Kenshi `hand`
including its index, serial, type, container, and container serial together
with plugin-process and game-session generations. The wire value is compared as
one opaque string; its layout is not an API. No pointer or display name is an
identity key.

The session generation advances on plugin startup and `GameWorld::resetGame`.
The full player-character selection set is exported separately from its primary
selection, and squad `selected` flags must match it. The Python schema rejects
missing session metadata, duplicate entity IDs, unknown selection IDs, or
selection disagreement when the capability is asserted.

The world-state store trusts and preserves validated native IDs. It continues
fingerprint/position normalization only for legacy producers without the stable
capability.

## Lifecycle

First observation is birth; the same ID in a later authoritative snapshot is an
update; omission from a later authoritative bounded list is a tombstone for
targeting. Omission does not prove death. Re-entry with the same still-valid
handle may reactivate the same ID. A handle serial or identity-session change
creates a different identity and prevents aliasing with the old lifetime.

## Consequences

- Duplicate names and native list reordering cannot choose identity.
- Exactly-one selection is mechanically testable.
- A missing, destroyed, reused, or prior-session target cannot silently become
  another character.
- Stable identity does not make the legacy native command acknowledgement
  causal. The next P5 boundary must add caller command IDs, mode/selection/
  target/revision fences, accepted/rejected reasons, and completion revisions.

## Evidence

Portable tests cover duplicate names, reordered source lists, tombstones,
session changes, legacy fallback, and strict selection consistency. The pinned
VS2010 SP1 Release x64 build passed.

A supervised live run on 2026-07-23 loaded protocol `0.2.0` and remained paused.
One primary selection, one selected-set member, and one squad flag agreed.
Eighteen nearby characters had eighteen IDs, including four distinct Ninja
Guards. Across later snapshots and a paused camera orbit, camera state changed
while the identity session, selected set, and complete nearby ID set did not.
That live query retained its list order, so native reorder behavior is not
claimed from the live run.

Later in the same process, Kenshi reset its DirectX device with an internal
graphics-driver-error reason. A controlled run with the prior plugin DLL
reproduced the identical error during normal exit after a ten-minute baseline.
With Low textures and water reflections disabled, this identity DLL then held
fresh telemetry for more than ten minutes and exited cleanly. This rules out
stable identity as a necessary cause of the observed reset, but it is not a
broad stability claim. The full incident record is in
`LIVE_STABILITY_INCIDENT_20260723.md`.
