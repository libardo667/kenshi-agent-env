# Food procurement

The first autonomous survival task is intentionally narrow: while paused in The
Hub, help the selected squad member buy one ordinary food item from a clearly
non-hostile trader, then return control with the game paused. The controller
must prefer declining or stopping over guessing at a person, dialogue option,
item, or price.

## Intended loop

1. **Recover the camera.** Double-click Lekko's first squad portrait to center
   the 3D camera on Lekko.
2. **Locate a trader.** Use the native role split to distinguish a real
   humanoid vendor leader from guards, followers, and trader-squad animals.
3. **Approach and talk.** Ask the bounded native bridge to issue Kenshi's own
   `PLAYER_TALK_TO` order for the nearest role-confirmed, non-hostile vendor,
   then advance through a short pause-bounded pulse.
4. **Enter trade.** Use the calibrated `choose_show_goods` envelope only when
   the captured dialogue visibly contains that exact first option.
5. **Buy one item.** Hover a candidate with `inspect_shop_item`, require a
   visible `[Food]` tooltip and affordable value, then use the separately
   bounded purchase skill with that value as `expected_price`.
6. **Verify and stop.** Require fresh telemetry to confirm the money change and,
   where available, the selected character's food-item count. Return paused.

All six stages have now passed one supervised live run. This is a narrow proof
for the current 1920x1080 HUD, Hub Barman dialogue, and Barman grid—not a claim
that arbitrary traders or resolutions are calibrated.

The camera anchor double-clicks Lekko's calibrated squad portrait. This removes
ambiguity when a building or other world object owns the detail panel, and uses
Kenshi's documented portrait-centering gesture without depending on keyboard
focus. Its normalized coordinate still assumes the standard bottom HUD and the
first portrait slot are visible.

Map zoom remains split into named, bounded skills that issue one wheel notch at
a small demonstrated map-canvas target. Raw scroll remains unavailable to the
planner. A live user gesture recorded zoom-in as delta `+120` at normalized
`(0.534, 0.505)`; this point sits below the town-information hover card that
intercepted an earlier probe. Wheel-down was also proved to change the map scale.

Even at maximum zoom, Kenshi's map remains a coarse regional view. It can confirm
The Hub and support settlement-scale travel, but it cannot identify the bar or
another individual business. Procurement must switch to a 3D-town survey after
arrival instead of spending turns reopening or zooming the map.

Local search also needs recoverable 3D framing. The current `camera zoom=0`
follow lock intentionally makes wheel zoom inert, so the live profile does not
advertise world-zoom skills. An unguided 18-turn food run exposed why this
matters: the planner spent every turn repeating an impossible zoom-out action
while the screenshot remained unchanged.

The truthful local camera vocabulary is `recenter_camera` with the live-proven
F binding, four bounded WASD pan skills, and two bounded Q/E orbit skills. Each
pan or orbit first recenters on selected Lekko, so the planner cannot accumulate
movement from an unknown camera anchor. Camera actions remain paused and do not
move Lekko. Every next observation now includes a bounded action-outcome ledger:
the chosen action, execution receipt, material frame-change score, meaningful
telemetry deltas, selected-character positions, and an explicit `no_op`
assessment when nothing tracked changed. This gives otherwise stateless planner
calls evidence that an attempted recovery failed and should not be repeated.

## Purchase policy

Before enabling a purchase action, the safety layer must enforce all of these:

- at most one item per action and a small per-run purchase limit;
- an explicit expected price supplied with the action;
- a configurable maximum item price;
- a configurable minimum money balance after the expected purchase;
- fresh money telemetry before the click and fresh post-action telemetry;
- a stop on a surprising money delta, unverified item identity, stale
  telemetry, or an unexpected screen transition.

The live profile now enforces one purchase per run, a maximum expected price of
750 cats, and a minimum expected post-purchase balance of 250 cats. It requires
fresh trade telemetry, exactly one verified non-hostile shop owner, and a
positive integer `expected_price`. Runtime success requires both a money
decrease and an increased selected-character `food_items` count. Item identity
still comes from the current visible tooltip, so the generic inventory-grid
problem remains deliberately unsolved.

The current telemetry bridge reports money, a basic food-item count, nearby
characters, faction disposition, dialogue/trade screen state, and normalized
screen positions for characters Kenshi says are rendered in the viewport. It
also keeps anatomy separate from trade roles. A safe vendor candidate is a
non-animal character whose platoon has a vendor list and who is both its leader
and dialogue-capable; `trader_squad` by itself is explicitly insufficient
because followers, guards, and animals inherit it. Kenshi's native
`PLAYER_TALK_TO` score reports whether the talk task is available at the current
range.

The plugin maintains a lifecycle-backed registry of exact
`ShopTrader::getTrader()` owners. Live validation established that the registry
is empty after save load: Kenshi creates those wrappers lazily when trade
inventory is requested. Exact ownership is therefore a post-interaction
verification signal, not a prerequisite for approaching a vendor. The bridge
still does not report inventory grids, item prices, affordable items, or
click-target occlusion. Visual evidence is necessary, but it is never a
substitute for a narrow click region and numeric spending guards.

The same plugin now provides one constrained control bridge:
`approach_confirmed_vendor`. It listens for a private `Ctrl+Shift+F10` chord on
the UI thread, repeats the vendor-role and disposition checks, and calls
`newPlayerTaskSelectedCharacters(PLAYER_TALK_TO, ...)` with the target handle,
indoor building, and position. Kenshi—not screen-coordinate guesswork—therefore
owns the route through the bar door and interior.

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

The 2026-07-23 proof completed the vertical slice:

- the native approach command reported `issued` with target `Barman`;
- a two-second pulse reduced Barman distance from 237.48 to 90.87;
- the next pulse opened Barman dialogue;
- the calibrated first option opened trade;
- lifecycle telemetry changed `active_shop_trader_count` from 0 to 1 and mapped
  `shop_inventory_owner: true` to Barman;
- hovering normalized `(0.316, 0.357)` showed `Meatwrap (2)`, `[Food]`,
  `50 nu`, and value `c.649`;
- one guarded right-click changed money from 1,000 to 351 and `food_items` from
  0 to 1; and
- the game was left paused.

Escape is the default recovery action for an unexpected overlay. If the screen
cannot be identified confidently after recovery, the run stops and leaves the
game paused.
