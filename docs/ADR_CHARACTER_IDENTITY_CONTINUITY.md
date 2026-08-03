# ADR: Character identity survives handle-container transitions

## Status

Accepted in protocol `1.7.0`. This supersedes only the player-character
container detail in `ADR_STABLE_NATIVE_IDENTITY.md`.

## Context

Kenshi can move one living squad character between native handle containers
while retaining its type, index, and lifetime serial. Treating the container as
part of character identity made that ordinary transition look like one squadmate
vanishing and another appearing.

## Decision

Character IDs combine the plug-in process/session generations with the
validated handle type, index, and lifetime serial. Container fields remain
zero-shaped placeholders so the opaque wire format does not change. IDs for
non-character handles retain the complete validated handle identity.

A process/session or character lifetime-serial change still creates a new
identity. Callers continue to compare the complete opaque string and never parse
its fields.

## Consequences

Squad, selection, and native-command references survive character streaming and
body-state container transitions. List reordering, duplicate names, reused
object lifetimes, and prior sessions still cannot silently alias a target.

Portable native conformance covers both sides of that boundary. Supervised live
acceptance remains separate.
