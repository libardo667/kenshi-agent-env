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
- PATCH changes fix producer/runtime behavior without changing the accepted wire
  shape or capability meaning. Exact terminal reason strings remain part of the
  contract even when a patch fixes when they are emitted.

The Python reader rejects a different major version. It accepts additive fields
only after the Pydantic schema is updated; strict validation is deliberate.

Current compatibility:

| Python package | Reader gate | Current matched producer | Evidence boundary |
| --- | --- | --- | --- |
| `0.1.0` | protocol major must be `0` | `0.6.1` | Portable parsing, shared Python/C++ timing and movement conformance, the pinned installed `0.6.1` DLL, and one exact live targetless-direction acceptance/completion run; an arbitrary future `0.x` producer may still fail strict model validation until Python is updated |

## Freshness

`captured_at` is UTC. `sequence` must increase for every emitted snapshot. A
reader marks telemetry stale based on wall-clock age. Continuous mode also
tracks duplicate sequences and preempts after the configured stall age/count,
which catches a frozen producer even if another process touches the file.

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
control.move_to_character
control.move_in_direction
```

Capabilities describe what the plugin can currently observe, not what exists in
the world.

Protocol `0.4.0` added `game.time`, exact dialogue target/options, and tooltip
evidence for the now-retired conditional food policy. Those fields remain part
of current `0.6.1` because the generic contracts use them. A closed or
unreadable dialogue serializes target/options as null, not an invented empty
choice list.

`ui.tooltip` exposes whether the shared MyGUI tooltip is visible, the joined
left/right line captions, and normalized bounds of the widget that caused that
tooltip. Those bounds bind a prospective click to the item currently supplying
the evidence; they do not describe or enumerate the rest of the inventory.
Text and bounds are null when no tooltip is visible.

Protocol `0.5.0` adds `ui.visible_controls`: a bounded list of at most 224
currently visible and enabled MyGUI controls. Buttons and text carry rendered
labels; inventory/shop item cells carry item name, value, quantity, type,
section, owner-window caption, and current bounds. Every entry includes a role,
window, and normalized current bounds. Traversal is additionally capped at
2,048 widgets and depth 32, and squad inventory enumeration at 64 items.
`item_value` is the item's base worth; it is not an authoritative shop asking
price or sale offer.

This is a semantic pointer anchor, not a direct MyGUI action surface. Python
selects an exact current label/role/window, must re-read the same binding inside
its input lease, and still acts through ordinary mouse input. Missing,
meaningfully ambiguous, changed, disabled, hidden, or wrong-owner matches emit
no click. The planner digest is sized from the observation/context budget and
groups controls by window; it does not silently apply a second fixed 64/120
entry cap.

The same revision exposes `stats_window_open`,
`management_screen_open`/`management_tab`, `open_inventory_windows`, named
squad inventory/equipment, nutrition reserve, blood, and combat state. The
legacy `active_screen` field still resolves only the title/world/inventory/
dialogue/trade hierarchy; management and stats use their dedicated fields.

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

`control.approach_vendor` is the legacy capability name for the generalized
dialogue-approach bridge; `control.approach_dialogue_target` is accepted as an
alias by Python. Protocol `0.5.0` also advertises
`control.move_to_character` and `control.move_in_direction`.
`LiveEnvironment` removes all native command capabilities and resets
`native_control` before constructing an `interface_only` observation. They are
planner-visible only in an explicitly configured `native_assisted` run.
Capability advertisement states producer intent and availability of the native
entry point; it is not by itself cross-language execution proof.

Python atomically replaces `native_command.request.json` before sending the
private `Ctrl+Shift+F10` bridge hotkey. Every strict request contains a globally
unique caller command ID, complete based-on world revision, control mode,
identity session, and exactly one selected stable ID. Targeted commands carry
one exact target stable ID; directional movement carries a bearing and a
distance capped at 2,000 units. The plugin reads requests only on the game/UI
thread and rejects malformed, duplicate, stale, wrong-mode/session/selection,
unavailable, replaced, role-invalid, or out-of-range work without choosing a
substitute.

Protocol `0.6.0` makes the command identity discriminated. Directional requests
and acknowledgements require an empty `target_id`, a bearing in `[0, 360)`, and
a positive distance no greater than 2,000. Targeted commands require a nonempty
stable target and zero direction fields. The production C++ parser and
acknowledgement serializer run against the same golden documents as the Python
models during every native build.

Protocol `0.6.1` corrects two runtime semantics without widening the command
surface. A newly accepted walk may remain paused across the next 500 ms
telemetry publication so Python can observe the keyed acceptance before its
bounded unpause pulse; only five seconds of uninterrupted later pause cancels
the command, and any unpaused update resets that window. Targeted walking still
uses the fixed arrival tolerance. Directional walking completes with
`walk_destination_reached` either inside that tolerance or after the selected
character crosses the destination plane along the intended vector. Sideways or
short blocked movement does not satisfy that crossing test.

For requests that pass every fence, the plugin uses Kenshi's own player-order API:
`PLAYER_TALK_TO` for approach and `MOVE_CUS_ORDERED` for character/directional
walking. Kenshi therefore owns pathfinding through doors and interior floors.
`native_control` keeps at most 16 acknowledgements keyed by command ID. Each
includes the request basis, acknowledgement/acceptance/terminal sequences,
exact target or direction vector, exact selection, status, and reason. Active
work cancels on selection mismatch, uninterrupted pause beyond the bounded
handoff window, target lifetime loss, or dialogue-role loss. Approach completes
only for dialogue bound to the exact target; targeted walking completes inside
the bounded arrival tolerance, while directional walking also accepts intended
destination-plane crossing. Python waits for the matching
acknowledgement on a later telemetry sequence; an old or different command
cannot certify execution. Legacy last-command fields remain diagnostics, not
causal authority.

## Identity

Protocol `0.2.0` introduced `identity.stable_handles`, retained by current
protocol `0.6.1`. When that capability is
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

Despite its field name, `squad[].hunger` is a nutrition reserve: `3.0` is full
and `0.0` is starving, matching Kenshi's UI value divided by 100. The native
`food_items` scalar has disagreed with observed carried items and must not
override the named `inventory` list. `squad.health` currently makes
life/down/conscious/crippled/combat, nutrition, and blood authoritative; it
does not make `bleeding_rate` or body-part wounds authoritative.

## Threading

Sample Kenshi objects only on a verified game/UI thread. Protocol `0.6.1`
uses separate Kenshi-owned `TitleScreen::update` and
`PlayerInterface::update` hooks. The former emits only title state and bounded
visible controls; it must not dereference `GameWorld`, player, camera, entity,
or native-command state. The latter emits loaded-game state only after
`GameWorld::initialized` is true. Direct MyGUI function detours and delegate
subscription are outside this contract. Serialize a plain copy.
Do not dereference Kenshi or MyGUI objects from a background writer thread. A
future worker may write copied bytes, but it must not retain game pointers.

## Privacy

Telemetry and screenshots can contain character names, save details, dialogue,
and user-authored mod content. Treat run directories as private by default.
