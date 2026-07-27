# Guide: telemetry wire protocol

The native plug-in is the producer and the Python runtime is the consumer, so
this contract has to hold across two languages. Field shapes live in
[`schemas/telemetry.schema.json`](../schemas/telemetry.schema.json), which is
generated and staleness-gated — this guide covers only the semantics a schema
cannot express.

## Transport and versioning

An atomic JSON file, normally
`%LOCALAPPDATA%\KenshiAgent\telemetry.latest.json`. The writer builds a complete
temporary file, flushes it, and replaces the public path; the reader never tails
or incrementally parses. A pipe or socket can be added later without changing
the semantics below.

`protocol_version` is `MAJOR.MINOR.PATCH`. MAJOR breaks existing readers or
changes field meaning. MINOR adds optional fields or capabilities. PATCH fixes
producer behavior without changing the accepted wire shape or capability
meaning; exact terminal reason strings are part of the contract and changing one
is not a PATCH.

`captured_at` is UTC and `sequence` increases on every emitted snapshot. Readers
mark staleness by wall-clock age; continuous mode additionally tracks duplicate
sequences and preempts after the configured stall age, which catches a frozen
producer even when another process touches the file.

## Capabilities gate meaning, not presence

A field may be emitted for debugging before it is reliable. The planner treats a
category as authoritative only when its capability is advertised. Absence means
unsupported — never permission to infer the value another way.

## Identity

With `identity.stable_handles`, `identity_session_id` is non-null and every
squad, selection, nearby, and native target ID derives from a validated Kenshi
`hand`, its lifetime serials, and the current process/session generations. **The
string layout is an internal plug-in detail**: compare the whole string, never
parse it for game meaning, and never use a raw pointer or display name as the
sole key.

The session generation advances when the plug-in starts or `GameWorld::resetGame`
begins a new/load transition; a process restart changes the process generation.
An ID is valid only inside its matching `identity_session_id` — a session change
tombstones every prior ID.

- **birth** — first authoritative observation of an ID in a session.
- **update** — a later observation carrying the same complete ID, even if name,
  position, role, or list position changed.
- **tombstone** — omission from a later authoritative bounded list, or any
  session change.

A nearby-list tombstone means "no longer in the observed set," not death or
destruction; target-bound execution must still cancel, because the exact target
is unavailable. A still-valid handle re-entering the query reappears with the
same ID; if Kenshi reuses the object lifetime, the serial changes and the ID
differs.

`ui.selected_character_ids` is the complete validated selection set;
`ui.selected_character_id` is the primary selection and must also appear in it.
Squad `selected` flags must match the set exactly, which makes an
exactly-one-selection precondition mechanically checkable instead of inferred
from a portrait name.

## World targets and current authority

`world_targets` reports structurally recognized non-character objects inside
the current bounded native query. A natural mine remains present when the
selected character cannot currently operate it. `context_actions` names its
reviewed semantic affordance; `task_available` and `task_probability` report
current Kenshi task eligibility separately.

Only targets with a current advertised action and `task_available: true` enter
the planner's actionable `context_targets` digest. Binding and native dispatch
recheck availability, exact identity, and structural role. Perception therefore
does not grant action authority.

If the native query reaches its maximum result count, `warnings` says
`world_targets` may be incomplete. Kenshi does not document that bounded query
as nearest-first, so omission at capacity must not be interpreted as absence.

## Partial and unknown values

Omit or null what is unavailable. Do not serialize unknown health as zero, an
unknown faction as neutral, or an unavailable inventory as empty. An empty list
is valid only when the capability says the list was actually enumerated.

Despite the field name, `squad[].hunger` is a **nutrition reserve**: `3.0` is
full and `0.0` is starving, matching Kenshi's UI value divided by 100. The
native `food_items` scalar has disagreed with observed carried items and must
not override the named `inventory` list. `squad.health` makes
life/down/conscious/crippled/combat, nutrition, and blood authoritative — it does
not make `bleeding_rate` or body-part wounds authoritative.

## Threading

Sample Kenshi objects only on a verified game/UI thread. The plug-in uses
separate Kenshi-owned `TitleScreen::update` and `PlayerInterface::update` hooks.
The title hook emits only title state and bounded visible controls and must not
dereference `GameWorld`, player, camera, entity, or native-command state. The
player hook emits loaded-game state only after `GameWorld::initialized`.
Serialize a plain copy; never dereference Kenshi or MyGUI objects from a
background writer thread. A future worker may write copied bytes but must not
retain game pointers.

## Privacy

Telemetry and screenshots can contain character names, save details, dialogue,
and user-authored mod content. Treat run directories as private by default.
