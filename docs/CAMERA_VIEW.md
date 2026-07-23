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

The lock freezes zoom input; 3D zoom skills must therefore not be offered in
the live profile. The truthful agent controls are `recenter_camera` (the
live-proven F binding), bounded WASD pans that recenter first, and bounded Q/E
orbits that recenter first. Rotation remains available, and moving Lekko can
also change a clipped follow view. To recalibrate distance, exit, restore
`camera zoom=125`, relaunch, choose a new distance, then repeat the lock step.

Community references:

- [Steam guide: Locked Height for New Perspective](https://steamcommunity.com/sharedfiles/filedetails/?id=2926728062)
- [Steam discussion showing the default `camera zoom=125`](https://steamcommunity.com/app/233860/discussions/0/4362373279649985286/)
