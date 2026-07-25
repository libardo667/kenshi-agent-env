# UI affordance coverage map

What the agent can reach through Kenshi's interface, what the interface could
expose but currently doesn't, and what stands between the two.

The agent can only bind to references telemetry actually advertises, so this is
an empirical document. Regenerate its evidence with:

```bash
python -m scripts.survey_ui_affordances --config <live config> --out runs/<dir>
```

Last surveyed: **2026-07-24**, protocol `0.5.0`, Hub Barman save, 1920x1080.

## Summary

| Interface | Enter | Observe contents | Interact | Exit | Verdict |
|---|---|---|---|---|---|
| Dialogue | ✅ via approach | ✅ options are labelled controls | ✅ proven live | ⚠️ untested | **usable** |
| Shopping / trade | ✅ via dialogue option | ❌ item grid not exported | ❌ no generic purchase | ⚠️ untested | **blocked** |
| Inventory | ⚠️ button advertised, unverified | ❌ item cells not exported | ❌ no item actions | ⚠️ untested | **blocked** |
| Map | ⚠️ button advertised, unverified | ❌ no map state at all | ❌ no map actions | ⚠️ untested | **blocked** |
| Squad / stats / tech | ⚠️ buttons advertised | ❌ no screen state | ❌ none | ⚠️ untested | **blocked** |
| World / camera | ✅ default | ✅ entities + HUD | ✅ approach only | n/a | **partial** |

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

### 1. `active_screen` still distinguishes only four states

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

### 3. Item cells are not MyGUI labelled widgets

Inventory and shop items are grid cells, not labelled controls, so neither
`visible_controls` nor any current action can name one. The existing
`inspect_shop_item` / `buy_inspected_shop_item` macros work around this with
**calibrated coordinates plus tooltip verification** — which is why purchase is
still scenario-only under `food_procurement_v1`.

*Needed:* export item cells (bounds + item name + stack/price) as first-class
references, so a generic `inspect_item` / `purchase_item` can bind to one the
way `activate_visible_control` binds to a control.

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
- `pointer_mode: relative` requires `--exclusive-input-session` when live
  actions are enabled.

## Reaching the long-form menu test

Ordered by what unblocks the most:

1. **Native: per-screen state.** Report map/squad/stats/tech so entering and
   exiting are observable and assertable. Without this, four of the five target
   interfaces cannot be verified at all.
2. **Native: scope and prioritize the control export.** Emit the active window's
   own widgets instead of letting HUD text consume all 64 slots.
3. **Action: `dismiss_screen`.** Exiting is currently Escape, a raw key the
   generic policy rejects. Needs to be a semantic action with a screen-state
   postcondition (which depends on 1).
4. **Native + action: item-cell references.** Unblocks inventory interaction and
   generic purchase — the second reusable chain.
5. **Live re-verification** with the rebuilt plug-in installed. The clicks and
   the pointer fix are confirmed working by direct observation; what remains is
   confirming telemetry now *reports* the screens that open.

Until map/squad/tech state exists, only dialogue is verifiable end to end —
though inventory and trade are now at least observable via
`open_inventory_windows` and a non-stale `active_screen`.
