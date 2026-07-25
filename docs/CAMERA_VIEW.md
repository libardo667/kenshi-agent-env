# Camera view for agent runs

Kenshi's normal camera can leave characters tiny, clip through roofs, and make
screen-grounded movement difficult. A community-discovered `settings.cfg`
setting can lock a useful close follow distance:

1. Leave `camera zoom=125` while calibrating.
2. Load the game, double-click the controlled character's portrait, and zoom to
   the desired follow distance.
3. Exit while the game is paused.
4. Back up `<Kenshi>/settings.cfg`, then set `camera zoom=0`.
5. Relaunch and confirm the same distance is restored before relying on it.

This sequence was live-validated on the current RE_Kenshi 1.0.65 setup. A close
over-the-shoulder view survived a full exit and `./dev launch` cycle. The file
did **not** need to be marked read-only: `camera zoom=0` persisted while Kenshi
updated its `continue` and `autosaveindex` entries normally. Making the whole
file read-only would also prevent legitimate preference and save-selection
updates, so it is not the default recommendation.

The lock freezes 3D zoom input. Legacy zoom macros should therefore not be
offered by a profile using it. The generic `use_game_binding` catalog still
contains Kenshi's zoom bindings because they remain meaningful on the map and
on profiles without the lock. Current plan conditions cannot author a camera
coordinate delta: `camera.position` is a capability name, and a field condition
using it is silently normalized to "the capability still exists." A later tick
can therefore satisfy that condition without camera motion. Receipts and
`recent_action_outcomes` may expose before/after camera facts, but a camera
binding must not be described as semantically proven until a real coordinate
condition or controller-owned effect predicate exists. To recalibrate distance,
exit, restore `camera zoom=125`, relaunch, choose a new distance, then repeat
the lock step.

Community references:

- [Steam guide: Locked Height for New Perspective](https://steamcommunity.com/sharedfiles/filedetails/?id=2926728062)
- [Steam discussion showing the default `camera zoom=125`](https://steamcommunity.com/app/233860/discussions/0/4362373279649985286/)
