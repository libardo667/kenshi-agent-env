# ADR: settlement entry owns the gate boundary

Status: accepted (2026-07-29)

## Context

Kenshi resolves a discovered town to a direction-dependent waypoint. A live
journey reached Squin's far-gate waypoint outside the walls, but the native
command reported `map_destination_reached`. Repeated travel returned to the
same point while the planner saw only occluded guards and no usable town
affordance. The other Squin gate happened to carry an earlier journey across
the threshold, so map-marker proximity was not a general arrival proof.

## Decision

`travel_to_map_destination` owns both the long approach and the mechanical
settlement-entry boundary. Reaching the town waypoint starts one ordinary
interior movement order; it does not complete the action.

Completion requires exact current-town identity. A gated town additionally
requires Kenshi's selected-character inside-walls state **after the
controller-owned interior leg reaches its destination**. A transient wall
crossing is progress, not arrival. An ungated town needs exact current-town
identity only. If the interior leg ends without the required proof, the command
cancels rather than inferring arrival from distance, names, nearby characters,
or a stopped path.

`game.location` continues to gate the player-readable location name.
`game.location.identity` separately gates the additive exact location ID and
inside-walls observation. This preserves older location-name observations while
allowing binders to reject repeated travel to the exact town already reached.

## Consequences

The controller may issue two Kenshi movement orders for one model-authored
destination, but it does not choose the destination or encode a town coordinate.
The planner receives the first usable post-entry state only after a deliberate
pause. Character awareness radius remains a separate perception decision; it
is not widened to compensate for premature movement completion.
