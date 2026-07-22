# Food procurement

The first autonomous survival task is intentionally narrow: while paused in The
Hub, help the selected squad member buy one ordinary food item from a clearly
non-hostile trader, then return control with the game paused. The controller
must prefer declining or stopping over guessing at a person, dialogue option,
item, or price.

## Intended loop

1. **Recover the camera.** Select and center squad member 1 by double-clicking
   the first squad portrait.
2. **Locate a trader.** Inspect the world view for a clearly non-hostile person
   with an obvious talk or shop affordance. Movement remains limited to the
   existing visible-terrain and map pulses.
3. **Approach and talk.** Right-click only that grounded visual target and allow
   a short, bounded approach pulse. If dialogue auto-pauses the game, preserve
   that paused state.
4. **Enter trade.** Select a trade dialogue option only after its actual layout
   has been captured and a dedicated click envelope has been calibrated.
5. **Buy one item.** Select one visibly identified food item only after its
   actual trade-grid layout has been captured and purchase guards are active.
6. **Verify and stop.** Require fresh telemetry to confirm the money change and,
   where available, the selected character's food-item count. Return paused.

The implementation is being exposed in those same stages. Camera recovery and
the bounded person interaction are available now. Dialogue and trade clicks are
deliberately absent until supervised runs provide real screenshots to calibrate
against.

The current focus target is calibrated against the 1920x1080 live client: the
first portrait center is at normalized `(0.349, 0.906)` inside a narrow portrait
envelope. A live double-click was accepted while Lekko was already centered and
left that valid view unchanged. Earlier keyboard probing showed that repeated
`1` selected Lekko but did not move the camera, so it is not used as the recovery
gesture.

Map zoom remains split into named, bounded skills that issue one wheel notch at
a small demonstrated map-canvas target. Raw scroll remains unavailable to the
planner. A live user gesture recorded zoom-in as delta `+120` at normalized
`(0.534, 0.505)`; this point sits below the town-information hover card that
intercepted an earlier probe. Wheel-down was also proved to change the map scale.

Even at maximum zoom, Kenshi's map remains a coarse regional view. It can confirm
The Hub and support settlement-scale travel, but it cannot identify the bar or
another individual business. Procurement must switch to a 3D-town survey after
arrival instead of spending turns reopening or zooming the map.

Local search also needs recoverable 3D framing. `zoom_world_out` and
`zoom_world_in` expose one wheel notch at a demonstrated central viewport target
only while the normal world HUD is visible. A live user gesture recorded
world-camera zoom-out as delta `-120` at normalized `(0.503, 0.523)`. The planner
should zoom out of clipped building geometry one observed step at a time before
it attempts grounded movement or person interaction.

## Purchase policy

Before enabling a purchase action, the safety layer must enforce all of these:

- at most one item per action and a small per-run purchase limit;
- an explicit expected price supplied with the action;
- a configurable maximum item price;
- a configurable minimum money balance after the expected purchase;
- fresh money telemetry before the click and fresh post-action telemetry;
- a stop on a surprising money delta, unverified item identity, stale
  telemetry, or an unexpected screen transition.

The current telemetry bridge reports money and a basic food-item count but does
not yet report inventory grids, dialogue, trade windows, nearby entities, or
faction disposition. Visual evidence is therefore necessary, but it is never a
substitute for a narrow click region and numeric spending guards.

## Interaction safety

Kenshi's direct right-click behavior is contextual: it can initiate friendly
conversation, but it can also attack an enemy. `interact_visible_person` is
therefore restricted to the world viewport and requires a clearly non-hostile
person with a talk/shop affordance. Ambiguity means `wait`, reposition, or stop.
There is no generic screen-click skill.

The interaction pulse also watches pause telemetry while it advances. Dialogue
can pause the game on its own; when that happens, the executor stops the pulse
without sending a second pause toggle that would resume the game.

## Calibration gates

Each new stage requires a supervised live proof before the next one is added:

- prove the bounded first-portrait double-click restores a useful camera view;
- capture a successful non-hostile interaction and its dialogue screen;
- use that capture to bound a dialogue-option action;
- capture the resulting trade screen;
- use that capture to bound a one-item purchase action;
- prove the purchase guard rejects over-budget, insufficient-balance, stale,
  repeated, and malformed requests in automated tests;
- perform one supervised low-cost purchase and verify the result.

Escape is the default recovery action for an unexpected overlay. If the screen
cannot be identified confidently after recovery, the run stops and leaves the
game paused.
