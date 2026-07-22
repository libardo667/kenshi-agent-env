# Movement skills

Live movement is intentionally split into two skills because Kenshi exposes two
different navigation scales through the same right mouse button.

## Fine movement: `move_visible_terrain`

Use this skill only when the screenshot visibly shows the 3D world and the map
and other overlays are closed. The target must be nearby, unobstructed terrain,
not a character, building, item, or UI element.

The skill accepts normalized `x` and `y` screenshot coordinates plus a
`duration_seconds` from 0.35 to 3.0 seconds, then emits one normalized right
click. Its calibrated safety envelope is:

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

It also accepts normalized `x` and `y` screenshot coordinates plus a
`duration_seconds` from 1.0 to 8.0 seconds, then emits one normalized right
click. Its calibrated safety envelope is:

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

Movement is also time-bounded. The model chooses a duration inside the
skill-specific range; 0.75 seconds for fine movement and 2.0 seconds for map
travel remain the defaults when it omits the argument. The live executor
requires a fresh paused observation before accepting either skill, executes the
destination command, briefly unpauses, and uses fresh native telemetry to
confirm both the unpause and final re-pause. All screenshot analysis and model
planning happen while paused. Model-selected direct unpause is blocked, so API
latency never becomes blind gameplay time.

The map skill closes the map before advancing. F12 is checked during each pulse;
if pressed, the pulse ends early and the executor re-pauses before reporting the
emergency stop. If re-pause cannot be confirmed, the episode terminates with an
environment error instead of starting another planner call.

## Polite shared input

Before capture or action execution, the Windows controller waits for a quiet
keyboard/mouse interval. It snapshots the foreground window and cursor, focuses
Kenshi for the atomic intent, then Alt+Tabs to the previous context before
restoring the saved cursor. Moving the pointer only after Kenshi loses focus
prevents its edge-scroll camera behavior from reacting to a secondary-monitor
or off-screen saved coordinate.
If keyboard/mouse activity or a focus change occurs during a movement pulse,
the agent treats that as the human taking a turn. It stops advancing, briefly
focuses Kenshi only to guarantee re-pause, then restores the human's latest
foreground window and cursor. Once input is quiet again it captures fresh state
and replans instead of retrying the interrupted intent. F12 remains the explicit
emergency stop.

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

An executor-controlled coarse pulse was then validated live. Run
`20260722T201337.162004Z` clicked a nearby map destination, closed the map,
advanced for 2.00 seconds, moved Lekko about 114 world units, and returned a
receipt only after telemetry again reported `paused: true`.

Run `20260722T201615.824597Z` then completed the active profile's full 30
decisions. Terra selected 14 fine pulses, two coarse map pulses, 12 camera
recenters, and two map opens. All 31 observations reported `paused: true`; no
action was rejected and no environment error occurred. Lekko's net displacement
was about 969 world units while remaining in The Hub area.

Run `20260722T210911.740414Z` validated planner-selected duration and polite
input leasing. Luna supplied `duration_seconds: 0.75` for a fine movement target;
the guard accepted it inside the configured 0.35–3.0-second range, and the
executor returned paused. The four-turn run completed with four executed
actions, no stale observations, rejections, or environment errors, and paused
final telemetry. Separate native probes confirmed exact foreground/cursor
restoration and preservation of a simulated human pointer handoff.
