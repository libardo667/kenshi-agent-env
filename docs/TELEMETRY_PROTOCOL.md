# Telemetry protocol

## Transport

The initial transport is an atomic JSON file, normally
`%LOCALAPPDATA%\KenshiAgent\telemetry.latest.json`. The writer creates a complete
temporary file, flushes it, and replaces the public path. The reader never tails
or incrementally parses the file.

A local named pipe or socket can be added later, but the file protocol remains a
useful compatibility and debugging surface.

## Versioning

`protocol_version` uses `MAJOR.MINOR.PATCH`.

- MAJOR changes break existing readers or change field meaning.
- MINOR changes add optional fields or capabilities.
- PATCH changes fix serialization without semantic change.

The Python reader rejects a different major version. It accepts additive fields
only after the Pydantic schema is updated; strict validation is deliberate.

## Freshness

`captured_at` is UTC. `sequence` must increase for every emitted snapshot. A
reader marks telemetry stale based on wall-clock age. Later work should also
track a non-increasing sequence, which catches a frozen plugin even when another
process touches the file.

## Capabilities

A field may be present for debugging while not yet reliable. The planner should
only treat a category as authoritative when the matching capability is present.
Examples:

```text
game.pause
game.speed
game.time
game.money
game.location
camera.position
squad.basic
squad.hunger
squad.health
squad.inventory
ui.modal
ui.dialogue
ui.dialogue.target
ui.dialogue.options
ui.tooltip
ui.visible_controls
nearby.characters
nearby.roles
nearby.shop_owners
control.approach_vendor
```

Capabilities describe what the plugin can currently observe, not what exists in
the world.

Protocol `0.4.0` adds the observations needed by the narrow conditional
food-procurement policy. `game.time` makes `game.elapsed_minutes` authoritative.
When dialogue is open, `ui.dialogue.target` exposes the stable ID of its exact
bound character and `ui.dialogue.options` exposes the bounded on-screen reply
captions in order. A closed or unreadable dialogue serializes these fields as
null, not an invented empty choice list.

`ui.tooltip` exposes whether the shared MyGUI tooltip is visible, the joined
left/right line captions, and normalized bounds of the widget that caused that
tooltip. Those bounds bind a prospective click to the item currently supplying
the evidence; they do not describe or enumerate the rest of the inventory.
Text and bounds are null when no tooltip is visible.

Protocol `0.5.0` adds `ui.visible_controls`: a bounded list of at most 64
currently visible and enabled MyGUI text/button widgets, each with its rendered
caption, role, and normalized current bounds. Traversal is additionally capped
at 2,048 widgets and depth 32. This is a semantic pointer anchor, not a direct
MyGUI action surface: Python may select only an exact configured label, must
re-read the same unique label and bounds inside its input lease, and still acts
through ordinary mouse input. Missing, duplicate, changed, disabled, or hidden
matches emit no click.

`nearby.characters` is limited to the plugin's bounded spatial query around the
selected character. An entity with `visible: true` is rendered inside the
current camera viewport and has a normalized `screen_position`. It may still be
hidden by a roof, wall, character, or other geometry, so this is not proof that
a click at that point will reach the character.
The current town-local query radius is 400 world units. This includes the Hub
Barman from a default Wanderer spawn while remaining bounded; role and
disposition checks, rather than name or coordinates, determine vendor
eligibility.

`camera_bearing_degrees` remains available for nearby entities that are outside
the viewport. It is measured around the current camera: zero is straight ahead,
negative values are left, positive values are right, and values near either
`-180` or `180` are behind the camera. This grounds a bounded orbit direction;
it is not evidence that the route to the entity is clear. Because the live
camera orbits around the selected character while facing inward, a negative target bearing is
brought toward zero with `orbit_camera_right`, and a positive target bearing
with `orbit_camera_left`. That sign convention was checked against the live
camera rather than inferred from the skill names.

`nearby.roles` keeps physical type separate from trade roles. `kind` is
`character` or `animal`; it is never inferred from squad commerce. The
`trader_squad` and `has_vendor_list` fields describe the entity's active
platoon, while `is_squad_leader`, `has_dialogue`, and
`talk_task_available` describe that exact character. The latter comes from
Kenshi's own `getPlayerTaskProbability(PLAYER_TALK_TO, ...)` query; its
companion `talk_task_probability` preserves Kenshi's score.

`nearby.shop_owners` means `shop_inventory_owner` is authoritative. Kenshi does
not keep `ShopTrader` objects in its spatial query and its `InventoryManager`
holds only one transient wrapper, so the plugin builds a bounded live registry
by hooking `ShopTrader` construction and destruction before a save loads. It
then compares each registered `ShopTrader::getTrader()` owner by pointer
against nearby characters. `active_shop_trader_count` reports the registry
size. Both values are null and the capability is absent if either lifecycle
hook fails.

`control.approach_vendor` is a narrowly constrained native control capability.
The plugin may emit it, but `LiveEnvironment` removes it and resets
`native_control` before constructing an `interface_only` observation. It is
planner-visible only in an explicitly configured `native_assisted` run.
Python atomically replaces `native_command.request.json` before sending the
private `Ctrl+Shift+F10` bridge hotkey. The strict request contains a globally
unique caller command ID, complete based-on world revision, control mode,
identity session, exactly one selected stable ID, and one exact target stable
ID. The plugin reads it only on the game/UI thread and rejects a malformed,
duplicate, stale, wrong-mode/session/selection, unavailable, replaced, or
role-invalid request without choosing a substitute.

After every fence passes, the plugin calls
`PlayerInterface::newPlayerTaskSelectedCharacters(PLAYER_TALK_TO, ...)` with
the character's handle, indoor building, and world position. Kenshi therefore
owns the pathfinding through doors and interior floors. `native_control` keeps
at most 16 acknowledgements keyed by command ID. Each includes the request
basis, acknowledgement sequence, exact target/selection, status, and reason.
Accepted commands also record their acceptance sequence. The active command is
cancelled if selection, target lifetime, or vendor-role evidence changes, and
is completed only when the open dialogue is bound to that exact target. Python
waits for the matching acknowledgement on a later telemetry sequence; an old
or different command cannot certify execution. Legacy last-command fields
remain diagnostic compatibility fields, not the causal authority.

## Identity

Protocol `0.2.0` introduced `identity.stable_handles`, retained by current
protocol `0.5.0`. When that capability is
present, `identity_session_id` is non-null and every squad, selection, nearby,
and native target ID comes from a validated Kenshi `hand`, its lifetime serials,
and the current process/session generations. The string layout is an internal
plugin detail. Consumers must compare the complete string and never parse it
into game meaning. No raw pointer or display name participates as the sole
identity key.

The native session generation advances whenever the plugin starts or
`GameWorld::resetGame` begins a new/load transition. A process restart also
changes the process generation. An ID is therefore valid only inside the
matching `identity_session_id`; a session change tombstones every prior ID.

Lifecycle terms are:

- **birth**: the first authoritative observation of an ID in an identity
  session;
- **update**: a later authoritative observation carrying the same complete ID,
  even if its name, position, role, or list position changed;
- **tombstone**: omission from a later authoritative bounded list, or any
  identity-session change.

A nearby-list tombstone means “no longer in the current observed set,” not proof
of death or destruction. Target-bound execution must nevertheless cancel
because the exact target is unavailable. If the same still-valid handle later
re-enters the bounded query, it may reappear with the same ID. If Kenshi
destroys/reuses the object lifetime, handle serial changes produce a different
ID.

`ui.selected_character_ids` is the complete validated player-character
selection set. `ui.selected_character_id` is the primary active selection and,
when present, must also occur in that set. Squad `selected` flags must match the
set exactly. This makes an exactly-one-selection precondition mechanically
checkable rather than inferred from a portrait name.

Snapshots without `identity.stable_handles`, including older `squad:<index>`
and `nearby:<index>` producers, retain provisional source IDs. The Python
world-state store continues its ambiguity-aware fingerprint/position
normalization only for those legacy sources. With the stable capability it
preserves native IDs exactly.

## Partial and unknown values

Optional values are omitted or null when unavailable. Do not serialize unknown
health as zero, an unknown faction as neutral, or an unavailable inventory as an
empty inventory. Empty lists are only valid when the capability says the list
was actually enumerated.

## Threading

Sample Kenshi objects only on a verified game/UI thread. Protocol `0.5.0`
subscribes to MyGUI's supported per-frame event so title-menu controls are
observable before a save creates `PlayerInterface`; loaded-game command
monitoring remains on `PlayerInterface::update`. Direct detours of third-party
MyGUI functions are outside this contract. Serialize a plain copy.
Do not dereference Kenshi or MyGUI objects from a background writer thread. A
future worker may write copied bytes, but it must not retain game pointers.

## Privacy

Telemetry and screenshots can contain character names, save details, dialogue,
and user-authored mod content. Treat run directories as private by default.
