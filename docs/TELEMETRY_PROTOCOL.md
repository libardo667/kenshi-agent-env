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
nearby.characters
nearby.roles
nearby.shop_owners
```

Capabilities describe what the plugin can currently observe, not what exists in
the world.

`nearby.characters` is limited to the plugin's bounded spatial query around the
selected character. An entity with `visible: true` is rendered inside the
current camera viewport and has a normalized `screen_position`. It may still be
hidden by a roof, wall, character, or other geometry, so this is not proof that
a click at that point will reach the character.

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

## Identity

`squad:<index>` is only a provisional episode-local identity. It can change when
squad order changes. The native implementation should eventually expose a
stable, non-address identifier derived from a validated Kenshi handle. Never
persist raw process pointers as autobiographical identity.

## Partial and unknown values

Optional values are omitted or null when unavailable. Do not serialize unknown
health as zero, an unknown faction as neutral, or an unavailable inventory as an
empty inventory. Empty lists are only valid when the capability says the list
was actually enumerated.

## Threading

Sample Kenshi objects only on a verified game/UI thread. Serialize a plain copy.
Do not dereference Kenshi or MyGUI objects from a background writer thread. A
future worker may write copied bytes, but it must not retain game pointers.

## Privacy

Telemetry and screenshots can contain character names, save details, dialogue,
and user-authored mod content. Treat run directories as private by default.
