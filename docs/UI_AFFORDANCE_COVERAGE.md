# Registered UI mechanism coverage

Current as of 2026-07-25. This document distinguishes broad interface
reachability from complete Kenshi gameplay support.

Regenerate the static action-surface audit with:

```bash
python scripts/audit_ui_affordances.py
```

The current result is **31/31 registry entries with a declared mechanism**, **0
missing exits**, and **1 pixel-fragile path** (the ESC menu is reached only as a
side effect). This is a presence audit, not complete affordance coverage. It
does not grade effect proof, mock parity, live verification, completeness of
suboperations, or cross-language executability; `move_in_direction` is the
current concrete case where a registered mechanism is not usable end to end.

The latest live UI survey was `runs/p7-ui-survey-6` on 2026-07-25, protocol
`0.5.0`, Hub save, 1920x1080. Later supervised runs added named item facts,
purchase/sale/equip actions, game bindings, and local movement after that survey.

## Current interface map

| Interface | Enter / exit | Observe | Interact | Current boundary |
| --- | --- | --- | --- | --- |
| World / camera | Base state | Nearby entities, camera, HUD | Pan/rotate/zoom, time, selection, dialogue approach, exact-character movement | Targetless direction is blocked; no remote map-position order |
| Dialogue | Approach / exact closing reply | Exact target and replies | Activate exact current reply | Escape does not end conversation |
| Inventory | `use_game_binding` / own close box | Owner window and named item cells | Scroll and equip | Drag/drop/move/drop is not modelled |
| Trade | Dialogue control / own close box | Seller/owner windows and named stock | Scroll, buy, sell | Trader money and exact sale offer are not exported |
| Map | Binding toggle | Management window, tab 0, controls | Pan/zoom; exact-character movement after closing | Targetless direction is blocked; no chosen remote map destination |
| Research/tech | Binding toggle | Management window, tab 2, buttons/text | Activate current controls | Domain semantics are not catalogued |
| Squad management | Current management controls | Management window, tab 4, buttons/text | Activate current controls | Recruit/reorder/dismiss workflows are not proven |
| Stats | Binding toggle | Dedicated open flag plus visible text | Read current export | Body-part wound model is incomplete |
| Message box | Opened by Kenshi / current control | Refusal text survives role-balanced budgeting | Acknowledge exact current control | Screen classification is indirect |
| ESC menu | Accidental Escape / exact Resume control | Current controls | Resume only | Entry is intentionally not planner-authorable |

Management tabs are one `ManagementScreen`, not separate top-level screens:
`management_tab` values observed live are 0 map, 2 research/tech, and 4 squad.
`active_screen` still names only title/world/inventory/dialogue/trade, so plans
must use `management_screen_open`, `management_tab`,
`stats_window_open`, and `open_inventory_windows` for the other windows.

## Planner-visible semantic actions

`src/kenshi_agent/action_contracts.py` is the intended authority. The current
catalog contains ten actions, advertised when the current control mode and
capabilities support them:

- `approach_dialogue_target`
- `move_to_character`
- `move_in_direction`
- `activate_visible_control`
- `dismiss_screen`
- `purchase_item`
- `sell_item`
- `equip_item`
- `use_game_binding`
- `scroll_screen`

Advertisement is not proof that every downstream layer agrees. In particular,
`move_in_direction` is advertised from the contract/capability pair but is
currently blocked by target assumptions in the C++ parser, Python
acknowledgement model, and monitored-option adapter.

Run control (`noop`, `wait`, `pause`, `set_speed`, whole-run `stop`) is separate.
Raw keys, hotkeys, cursor moves, clicks, and scroll primitives are controller
implementation details and are rejected by the generic live policy.

Each semantic action binds evidence instead of a model-authored coordinate.
Controls bind exact label/role/window; items also bind item facts and owner
window; working movement binds an exact identity, while the intended
bearing/distance binding is blocked downstream. The same binding is recomputed
inside the input lease.

## Visible-control export

Protocol `0.5.0` emits up to **224** current controls, not the former 64:

1. buttons, including caption-less icon buttons by widget name;
2. named inventory/shop item cells from the inventory structure;
3. text, including dialogue choices and refusal messages.

Every control carries normalized bounds and its owning window. Item cells also
carry name, value, quantity, type, and section. The planner digest groups
controls by window and adds `belongs_to: vendor` plus `seller_id`, or
`belongs_to: you`, when ownership is known.

The Python digest has a 4,096-entry pathological backstop. Its working size is
derived from the room in the observation/model context and balanced across
roles. The configured observation budget is a spending preference and does not
silently remove actions; only the real model context ceiling may truncate the
surface, and the payload states the count and consequence when it does.

Duplicate item cells are considered ambiguous only when their facts differ.
Interchangeable stock—same window, item, and value—remains bindable. Duplicate
buttons remain distinct and fail closed unless the window disambiguates them.

## Current movement boundary

The usable generic movement path is narrower than the registry count implies:

- `move_to_character` walks to an exact current nearby character within the
  400-unit query.
- `move_in_direction` is intended to walk clockwise-from-north bearing plus
  distance, capped at 2,000 units, without naming any target. Python produces
  that targetless request, but the native parser/acknowledgement and executor
  option paths currently require a target, so it is not usable end to end.

Working native movement uses Kenshi's own `MOVE_CUS_ORDERED` pathing and
finishes on bounded arrival. An empty place can still strand the generic agent
when there is no nearby character. Long-distance map travel is a separate gap:
choosing a remote map position and issuing its right-click order has no action.

## Facts and exploration cost

Audit the facts needed to make decisions with:

```bash
python scripts/audit_fact_coverage.py --telemetry <telemetry.latest.json>
python scripts/audit_fact_coverage.py --log runs/<run>/events.jsonl
```

The audit distinguishes exported, discoverable-by-action, dark, and
not-applicable facts. The early Hub trade measurement cost ten exploratory
actions because hunger, inventory, and item identity were missing. After the
plugin began exporting squad nutrition/blood/inventory and named shop cells,
the same measured decision surface reached **zero exploratory actions**.

Important semantics:

- `hunger` is nutrition reserve: 3.0 full, 0.0 starving.
- The native `food_items` scalar has disagreed with live inventory; prefer the
  named inventory list.
- `in_combat` is now emitted. Blood is emitted; `bleeding_rate` and body-part
  wounds are not yet authoritative.
- Inventory sections distinguish carried gear from worn/wielded gear.

Still dark or incomplete: current task/goal, location name, trader money,
remote world/map state, body-part wounds, getting-eaten, and geometry
occlusion.

## Live-earned control rules

- Escape is not a generic back action. With no closable modal it opens Kenshi's
  ESC menu. Inventory/trade close through their own derived close box;
  management/stats screens toggle through their own game binding.
- Kenshi ignores a zero-duration MyGUI click. Semantic control activation uses
  the measured hold duration.
- Relative pointer motion is game input and can pan the camera. The controller
  synchronizes the OS/game cursors from a known corner rather than trusting an
  absolute warp.
- Restoring the cursor after a relative-mode lease desynchronizes Kenshi, so the
  final cursor stays in place. Exclusive input mode is required for live
  relative-pointer execution.
- A purchase/sale receipt proves delivery, not effect. The plan must verify a
  causally later money change.
- A cell's `item_value` is base worth rather than an authoritative final shop
  charge. Optional pre-purchase caps use the declared estimate; only the later
  money delta reveals the actual debit.
- Equipping and selling share a right-click gesture. `equip_item` refuses while
  trade is active and repeats that check inside the lease.

## Remaining work

- Add a semantic long-distance map travel order.
- Model drag/drop, move-between-sections, and drop-item operations.
- Expand management-screen domain semantics without treating visible text as
  permission for an irreversible action.
- Re-survey at an alternate resolution and across additional mod/UI layouts.
- Continue reducing the need for legacy profile-calibrated macros in favor of
  current semantic references.
