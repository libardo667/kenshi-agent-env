# Movement skills

Live movement is intentionally split into two skills because Kenshi exposes two
different navigation scales through the same right mouse button.

## Fine movement: `move_visible_terrain`

Use this skill only when the screenshot visibly shows the 3D world and the map
and other overlays are closed. The target must be nearby, unobstructed terrain,
not a character, building, item, or UI element.

The skill accepts normalized `x` and `y` screenshot coordinates and emits one
normalized right click. Its calibrated safety envelope is:

```text
x: 0.15 .. 0.85
y: 0.15 .. 0.65
```

At the calibration resolution of 1920x1080, that corresponds to client pixels
`x=288..1632`, `y=162..702`. It excludes the bottom command interface, the left
building panel, most right-side controls, and the pause banner.

## Gross movement: `move_on_map`

Use this skill only when the screenshot visibly shows the open map. The target
must be inside the map canvas, away from the window frame, tabs, and scrollbars.

It also accepts normalized `x` and `y` screenshot coordinates and emits one
normalized right click. Its calibrated safety envelope is:

```text
x: 0.30 .. 0.68
y: 0.16 .. 0.69
```

At 1920x1080, that corresponds to client pixels `x=576..1306`, `y=173..745`.
The observed map canvas was approximately `x=540..1332`, `y=145..766`, so the
configured rectangle is deliberately inset.

## Enforcement and current limitation

The OpenAI planner receives a structured skill specification containing each
skill's arguments and visual precondition. The live action guard independently
expands the selected skill and rejects pointer output outside its configured
envelope. Direct click actions remain blocked by the active profile.

The Windows controller submits absolute cursor placement and mouse-button
events in one `SendInput` batch. This prevents physical cursor movement from
interleaving between placement and the click and silently redirecting a command.

Native telemetry does not yet report whether the map is open. Consequently,
the map-open/map-closed precondition is currently grounded in the captured
frame and planner instructions, while the coordinate envelope is enforced in
code. Until active-screen telemetry or a deterministic visual classifier is
available, movement should be supervised and recalibrated after meaningful
changes to resolution, window mode, or UI scale.

## Initial live trial

The first supervised trial on 2026-07-22 validated both paths while Kenshi was
paused between short movement windows:

- Fine movement reached speed 54.0 and changed Lekko's position by about 26.7
  world units toward nearby visible terrain.
- Coarse map movement reached speed 72.6 and changed position by about 165
  world units toward a point southeast of The Hub marker.
- The first map attempt exposed a cursor-placement race because physical mouse
  movement could occur between separate synthetic move and click calls. Atomic
  move-plus-button injection fixed it; a subsequent frame showed the cursor at
  the requested map pixel.
- Kenshi was explicitly re-paused, the map was closed, and later telemetry
  confirmed Lekko's position remained stable.
