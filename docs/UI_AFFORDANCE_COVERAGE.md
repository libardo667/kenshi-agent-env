# UI affordance coverage map

What the agent can reach through Kenshi's interface, what the interface could
expose but currently doesn't, and what stands between the two.

The agent can only bind to references telemetry actually advertises, so this is
an empirical document. Regenerate its evidence with:

```bash
python -m scripts.survey_ui_affordances --config <live config> --out runs/<dir>
```

Last surveyed: **2026-07-25** (`runs/p7-ui-survey-6`), protocol `0.5.0`,
Hub save, 1920x1080, with the management-screen export installed.

## Summary

Every screen below was **opened and closed successfully** by clicking its own
advertised HUD button — entering and exiting work. The differences are in what
the agent can *observe* once inside.

| Interface | Enter | Exit | Screen state | Contents | Interact | Verdict |
|---|---|---|---|---|---|---|
| Dialogue | ✅ via approach | ✅ `dismiss_screen` | ✅ `dialogue` | ✅ options are controls | ✅ proven live | **usable** |
| Inventory | ✅ `INV` toggles | ✅ toggle / `dismiss_screen` | ✅ `inventory`, `open_inventory_windows: 1` | ✅ `ARRANGE` + item cells | ✅ activate a cell | **navigable + interactive** |
| Map | ✅ `MAP` toggles | ✅ toggle | ✅ `management_screen_open`, `tab: 0` | ⚠️ `+` / `-` zoom | ❌ no map actions | **navigable** |
| Tech | ✅ `TEC` toggles | ✅ toggle | ✅ `management_screen_open`, `tab: 2` | ✅ 8 category buttons | ⚠️ buttons only | **navigable** |
| Squad | ✅ `SQD` toggles | ✅ toggle | ✅ `management_screen_open`, `tab: 4` | ⚠️ `ADD SQUAD`, `DELETE` | ⚠️ buttons only | **navigable** |
| Stats | ✅ `STA` toggles | ✅ toggle | ✅ `stats_window_open: true` | ❌ no new controls | ❌ none | **navigable** |
| Shopping / trade | ✅ via dialogue option | ✅ `dismiss_screen` | ✅ `trade` (staleness fixed) | ✅ item cells exported | ✅ hover/activate a cell | **navigable + interactive** |
| World / camera | ✅ default | n/a | ✅ `world` | ✅ entities + HUD | ✅ approach only | **partial** |

**Management tabs are one window, not separate screens.** `MAP`, `TEC`, and
`SQD` all open `ManagementScreen`; `management_tab` distinguishes them.
Empirically: **0 = map, 2 = tech/research, 4 = squad**. This is why they all
reported `world` before — `active_screen` structurally could not express them.

Assertable from a plan via the condition paths
`telemetry.ui.management_screen_open`, `telemetry.ui.management_tab`,
`telemetry.ui.open_inventory_windows`, `telemetry.ui.stats_window_open`.

Baseline world HUD advertises 21 buttons: `INV STA MAP TEC SQD HLP`,
`BLOCK HOLD PASSIVE JOBS RANGED TAUNT SNEAK`, `MEDIC RESCUE PROSPECT`, `X`, and
three tutorial entries.

**The export cap is still saturated at 64 on every screen**, so text is being
truncated even with buttons prioritized. Raising `MAX_VISIBLE_UI_CONTROLS` or
scoping to the active window remains worthwhile.

## What the agent has today

Two planner-visible semantic actions (see `action_contracts.py`):

- `approach_dialogue_target(target_id)` — any current non-hostile person with
  dialogue. Proven live end to end.
- `activate_visible_control(exact_label, role)` — any **uniquely labelled**
  current control. Proven live on a dialogue option.

Plus run control (`stop`, `noop`, `wait`, `pause`, `set_speed`). Raw
click/key/hotkey are rejected by the generic policy.

Observation surface: `dialogue_targets`, `visible_controls`, `semantic_actions`,
`telemetry.ui.*`, `active_shop_trader_count`, tooltip text and source bounds.

## The three structural gaps

### 0. RESOLVED 2026-07-24: the agent was blind to menus it successfully opened

The first survey reported every screen as identical. The operator confirmed the
menus **were** visibly opening. Two independent causes, both now fixed:

- **`active_screen` was stale.** `tradeOpen` tested only whether the trade
  character handles were non-null, and Kenshi does not clear them when the
  window closes — so telemetry reported `trade` indefinitely after one trade,
  and the agent could never observe leaving the shop. It now also requires an
  actually open inventory window.
- **The control export was saturated by HUD text.** The walk was
  first-come-first-served into a 64-slot cap, and persistent HUD/stat labels
  consumed all of it, so a newly opened window's own widgets never appeared.
  The export now runs **two passes — buttons first, then text** — each with its
  own visit budget, so interactive affordances can no longer be crowded out.

Also added: `ui.stats_window_open` and `ui.open_inventory_windows`.

### 1. RESOLVED 2026-07-25: management screens are now observable

`ManagementScreen::getSingleton()` exposes `getVisible()` and `getCurrentTab()`.
Map, squad, research and factions are **tabs of that one window**, which is why
no amount of `active_screen` values could have described them. Now exported as
`management_screen_open` + `management_tab`.

### 1b. `active_screen` still distinguishes only four states

`KenshiAgentTelemetry.cpp` derives it as:

```
dialogue > trade > inventory > world
```

from `gui->dialogue->isVisible()`, the trade character handles, and
`gui->isAnyInventoryWindowOpen()`. **Map, squad, stats, and tech have no state
at all** — with the map open, telemetry still reports `world`. The agent cannot
tell whether it opened the map, so it cannot verify entering or exiting it, and
no success condition can be written about it.

*Needed:* map/squad/tech states. `MapScreen` and `SquadManagementScreen` hang
off `ManagementScreen` with no exposed visibility accessor, so this needs a
located instance — and this project has already crashed Kenshi twice on
speculative GUI hooking, so it deserves its own careful slice rather than a
guess. `characterStatsWindowVisible()` and `getNumOpenInventoryWindows()` were
available and are now exported.

### 2. The visible-control export is capped and saturated

`MAX_VISIBLE_UI_CONTROLS = 64`, and on the trade screen the survey found the cap
**fully consumed** — 64 of 64 — almost entirely by persistent HUD and character
stat text: body-part rows, XP percentages, `#000000` colour codes, `Floor 0`,
clock, money. The trade window's own item grid and its buttons never appear.

Consequences:
- Screen-specific affordances are crowded out by static HUD chrome.
- Duplicate labels (`100` appears seven times) correctly fail closed as
  ambiguous, but they are also consuming export slots.
- 16 buttons are advertised and reachable: `INV STA MAP TEC SQD HLP`,
  `BLOCK HOLD PASSIVE JOBS RANGED TAUNT SNEAK`, `MEDIC RESCUE PROSPECT`.

*Partly addressed* by the two-pass button-priority export above. Still needed:
scoping to the active window's subtree, and richer roles — dialogue options
currently report as `text`, indistinguishable from a stat label.

### 3. RESOLVED 2026-07-25: item cells are exported

Inventory and shop items are `MyGUI::ImageBox` icons, not `TextBox` widgets, so
the text-only export could never see them — the grid looked *empty* rather than
crowded out. A third export pass now emits them as role `item`, gated to fire
only while an inventory or trade window is open so the world view is not flooded
with decorative images.

Verified live: an open inventory reports `{'button': 25, 'item': 7, 'text': 32}`
with real per-cell bounds.

They carry no caption of their own, so each is labelled by its ordinal in the
deterministic export walk (`item_0`, `item_1`, …) and `activate_visible_control`
accepts `role: "item"`. **What a cell contains must still be read from the
tooltip after hovering it** — the ordinal identifies a position, not an item.
That is the same tooltip-grounded evidence the calibrated purchase path already
required, now reachable without model-authored coordinates.

*Still needed for generic purchase:* bind the tooltip readback to the hovered
cell as one action, so price and item name are verified against the exact cell
being bought.

## Navigation findings

- **Escape is not a "back out" key.** With nothing open it *opens* Kenshi's ESC
  pause menu (`RESUME`, `SAVE GAME`, `LOAD GAME`, `NEW GAME`, `OPTIONS`, `EXIT`).
  The first surveys silently ran with that menu open, which blocked the clicks
  underneath and corrupted the "world" baseline. Screens should be closed by
  toggling their own HUD button; reserve Escape for dialogue/trade/inventory,
  and leave the ESC menu via `RESUME`.
- The HUD screen buttons **toggle**, so the same reference opens and closes.

## Control-layer findings

- **Kenshi ignores a zero-duration click.** MyGUI needs a real press; the
  calibrated macros always used `hold_seconds: 0.12`. Now
  `controls.control_activation_hold_seconds`.
- **Relative pointer traversal is itself game input.** In `relative` pointer
  mode the cursor was stepped 12 px at a time from wherever it sat to the
  target; dragging across the 3D view made Kenshi pan the camera, and the
  cursor-restore-then-walk-back cycle repeated it on every click. The 2026-07-24
  survey panned the camera far to the left purely as a side effect. Fixed by
  warping to just short of the target and using relative steps only for the
  final resync (`relative_pointer_warp_*`).
- **Restoring the cursor on handback desynchronizes Kenshi.** In relative mode
  Kenshi tracks its drawn cursor from motion deltas, so warping the OS cursor
  back to the human's old position left the two disagreeing: the operator's next
  small movement made Kenshi's cursor jump to a screen edge and pan the camera.
  The cursor is now left where the agent finished in relative mode.
- `pointer_mode: relative` requires `--exclusive-input-session` when live
  actions are enabled.

## Auditing what the agent can know

Affordances are only half of it: an action the agent cannot *decide* to take is
as good as absent. `kenshi_agent.fact_coverage` names the facts playing Kenshi
actually requires and classifies each against a live snapshot:

```bash
python -m scripts.audit_fact_coverage --telemetry "$LOCALAPPDATA/KenshiAgent/telemetry.latest.json"
python -m scripts.audit_fact_coverage --log runs/<run>/events.jsonl
```

- **exported** — in the snapshot, free.
- **discoverable** — obtainable only by acting, at roughly one model round-trip
  (~20 s) each, with a staleness risk while the answer arrives.
- **dark** — no route at all, so any goal needing it is unreachable.
- **n/a** — the context cannot speak to it (dialogue options with no
  conversation open), so it is not counted against coverage.

The headline number is **exploration cost**: how many agent actions it takes to
learn everything not already exported. On the Hub trade screen it was **10** —
about three minutes of model calls before the agent could make one decision.

Measured 2026-07-25 with a trade window open:

| State | Facts |
|---|---|
| exported | `world.money`, `ui.visible_controls`, `ui.screen`, `nearby.dialogue_targets` |
| discoverable (10 actions) | `self.hunger`, `self.inventory`, `self.health`, `self.first_aid_kits`, `shop.item_names`*, `shop.item_price`, `shop.item_category`, `shop.item_quantity` |
| dark | `self.current_goal`, `world.location_name`, `world.clock`, `shop.trader_money` |

\* item names are exported by the newest plug-in build; the measurement above
predates installing it.

Two things stand out. The agent is **blind to itself** — it set itself the goal
"secure affordable food for Hep" while unable to read Hep's hunger, inventory or
health. And **every shop fact costs a hover**, which is why one run spent 21
planning cycles probing cells instead of buying anything.

## Reaching the long-form menu test

**All four target interfaces — map, inventory, dialogue and shopping — can now
be entered, navigated, interacted with, and exited**, all verified live and all
assertable from a plan. A long-form menu test is buildable on this surface.

Remaining refinements, none of them blockers:

- **Tooltip-bound item inspection.** Hovering an exported cell populates the
  tooltip; binding that readback to the exact hovered cell in one action is what
  generic at-most-once purchase should be built on.
- **Richer roles.** Dialogue options still report as `text`, indistinguishable
  from a stat label.
- **Export scoping.** The 64-slot cap still binds on busy screens; scoping to
  the active window's subtree would be cleaner than global priority passes.
