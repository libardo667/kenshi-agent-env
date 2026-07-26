# ADR: persistent camera lock and bounded recovery

## Context

Kenshi's normal camera leaves characters tiny, clips through roofs, and makes
screen-grounded movement unreliable. Runs need a stable close view, and when the
view is lost the agent needs a way back that does not become the model
improvising camera keys indefinitely.

## Decision: lock the follow distance in `settings.cfg`

1. Leave `camera zoom=125` while calibrating.
2. Load the game, double-click the controlled character's portrait, zoom to the
   desired follow distance.
3. Exit while paused.
4. Back up `<Kenshi>/settings.cfg`, then set `camera zoom=0`.
5. Relaunch and confirm the distance is restored before relying on it.

The file does **not** need to be read-only: `camera zoom=0` persists while Kenshi
updates its `continue` and `autosaveindex` entries normally. Marking the whole
file read-only would block legitimate preference and save-selection updates, so
it is not recommended. To recalibrate, restore `camera zoom=125`, relaunch,
choose a new distance, and repeat the lock step.

Validated on RE_Kenshi 1.0.65: a close over-the-shoulder view survived a full
exit and `./dev launch` cycle.

## Decision: recovery is controller-owned, not model-composed

The lock freezes 3D zoom input, so profiles using it must not offer legacy zoom
macros. The generic `use_game_binding` catalog still carries Kenshi's zoom
bindings because they remain meaningful on the map and on unlocked profiles.

The no-argument `recover_camera_view` action owns the whole best-effort
transaction. It accepts an already-clear character-anchored frame without input.
Otherwise it leaves the world paused, double-clicks the selected portrait, scores
the character floor and at most two lower floors, restores the best, and compares
one fixed End/Q/E/comma/period candidate sequence. End may be inert under the
zoom lock; it stays a bounded candidate and never a model-authored adjustment.
The receipt retains every scored frame path and hash and returns exactly
`already_clear`, `recovered`, or `failed_after_bounded_attempts`.

## Consequences

A failure verdict means the bounded policy exhausted its options. The model must
not start composing camera keys to continue the same recovery. Run
`20260725-camera-recovery-live-02` demonstrated the boundary in a ruined Storm
House: floor 0 was already lowest, End was inert under the lock, and every fixed
orbit/tilt candidate stayed occluded by building mesh. The controller chose the
least-obstructed frame and stopped after ten primitives with the game paused.
Walking the character outside would have been gameplay, not camera recovery.

Plan conditions cannot author a camera coordinate delta: `camera.position` is a
capability name, and a field condition on it is silently normalized to "the
capability still exists," which a later tick satisfies without any camera motion.
Receipts and `recent_action_outcomes` may expose before/after camera facts, but a
camera binding must not be described as semantically proven.

References: [Steam guide: Locked Height for New
Perspective](https://steamcommunity.com/sharedfiles/filedetails/?id=2926728062),
[default `camera
zoom=125`](https://steamcommunity.com/app/233860/discussions/0/4362373279649985286/).
