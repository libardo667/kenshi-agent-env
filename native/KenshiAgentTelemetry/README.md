# KenshiAgentTelemetry native plug-in

`KenshiAgentTelemetry.dll` is the loaded-game telemetry and bounded control
bridge between Kenshi and the Python runtime. This file describes the current
source contract. Historical protocol milestones belong in Git history and named
run evidence, not in a second current protocol narrative.

## Current protocol

```text
telemetry protocol       1.18.0
native request schema    1.4
loaded-world capabilities 43
active native commands  at most one
```

The source declarations are authoritative. `KenshiAgentTelemetry.cpp` owns the
telemetry version, `NativeCommandRequest` in
`src/kenshi_agent/core/transport.py` owns the strict Python request schema, and
`GameplayCapabilities.json` owns the loaded-world capability vocabulary. The
portable consistency tests and the native conformance executable compare the
shared JSON fixtures with both protocol implementations.

Protocol 1.18.0 is additive over the earlier 1.x telemetry family. The proposed
2.0 roster/platoon/plural-command redesign in the interaction-scope plan has not
landed. Current telemetry still serializes player characters under `squad`, and
`native_control.active_command_id` plus one native active-command record still
limit the controller to one monitored command at a time.

## Hooks and telemetry

The plug-in installs hooks on Kenshi's own game/UI thread:

- `TitleScreen::_NV_update` publishes a minimal title/control snapshot.
- `PlayerInterface::update` monitors native commands and publishes loaded-world
  telemetry after the original update returns.
- `ContextMenu::showContextMenu` plus a muted `ContextMenuGUI::show` hook let the
  plug-in ask Kenshi which orders it would display without drawing a menu over
  the operator's game.
- `ShopTrader` lifecycle hooks maintain exact shop-owner identity, and
  `GameWorld::resetGame` clears session-bound registries and acknowledgements.

Loaded telemetry includes clock and location state, camera state, complete
current selection, squad vitals/inventory/task summaries, nearby characters and
roles, bounded world targets, discovered map destinations, dialogue and tooltip
state, visible controls, every currently exported open inventory, and keyed
native command acknowledgements. Stable IDs are derived from validated Kenshi
handles plus process/session generations; names and list positions are not
identities.

Bounded collections expose completeness or warning evidence where the model
supports it. An empty bounded result must not be generalized beyond that stated
boundary.

## Request transport

Python atomically replaces
`%LOCALAPPDATA%\KenshiAgent\native_command.request.json`. The title and player
update hooks watch the file's last-write time, wait one frame after a change so
an atomic replacement cannot be read halfway through, and then parse and
dispatch the request on Kenshi's UI thread.

`Ctrl+Shift+F10` is no longer required to wake the plug-in. It remains an
optional manual/diagnostic signal for `scripts/dispatch_native_command.py`.
The current Python gameplay path still sends that short hotkey after publishing
most native gameplay requests, so the supported path is presently redundant:
file-change dispatch plus a compatibility trigger. Native pause and speed
control use the file-change path without a hotkey. Startup, recovery, emergency
stop, and host-safety paths may still use Windows input when the native side is
absent or cannot be trusted to stop itself.

Every request carries:

- a strict schema version and UUID-shaped command ID;
- `native_assisted` control mode and the current identity session;
- the complete authored selected-recipient basis, except for commands that name
  their own recipient;
- the telemetry revision on which dispatch authority was based; and
- only the fields belonging to that command's wire projection.

The plug-in rejects duplicate IDs, future or stale revisions, session mismatch,
malformed command shapes, conflicting UI state, changed targets, and failed
command-specific authority. Acknowledgements are keyed and carry accepted and
terminal telemetry sequences where applicable. An acknowledgement proves what
the plug-in accepted or observed at its terminal boundary; it is not by itself
proof of an intended later world outcome.

## Current command surface

The loaded-world protocol currently covers:

- exact selection, regrouping, nearby movement, directional movement, building
  exit, and map travel;
- exact dialogue approach, resource context actions, generic character orders,
  resource production, and local resource survey;
- paired trade/loot/resource windows and item transfer;
- elective body shifting;
- pause and speed control; and
- recovery-only trade-window close plus the diagnostic body-platoon probe.

`close_trade_window` and `shift_body_platoon` have no planner-visible operation
definition. They are recovery/diagnostic routes, not hidden fallbacks.

The wire name `approach_confirmed_vendor` is historical. Its current operation
is `approach_dialogue_target`, and it may target any exact conscious,
non-hostile character currently confirmed talkable.

The native bridge still holds one active command. Selection is captured in the
request but several native monitors still depend on the current selection, and
separate retained commands for disjoint recipient groups are not implemented.
Those open limits are tracked in the
[interaction scope and lifecycle record](../../docs/KENSHI_INTERACTION_SCOPE_ORDER_LIFECYCLE_PLAN.md).

## Inventory and shop semantics

`open_trade_window` calls `ForgottenGUI::showTradeWindow` with two exact owner
handles and Kenshi's `TradeWindowType`, then waits for both inventory windows to
be observed. Acceptance of that call is not the same as observing the windows.

`transfer_item` does **not** call `InventoryGUI::RClickAutoTrade`. Live attempts
showed that the KenshiLib declaration does not safely match the shipped binary
for out-of-band use. Current code resolves the source item with
`InventorySection::getItemAt`, removes it through
`Inventory::removeItemDontDestroy_returnsItem`, and adds it through
`Inventory::tryAddItem`. It checks model capacity, attempts rollback on a
refusal, and reports a partial transfer if later destination counts prove that
some goods moved despite a false return.

When `InventoryGUI::getNPCTrader()` reports a shop trade, the project applies
its own simplified economics. The receiving side determines buy versus sell;
`Item::getValueSingle` supplies the unit value; the destination's before/after
`Inventory::getNumItems` count determines how many actually arrived; and money
moves between the two inventories for that measured count. This does not
reproduce Kenshi's haggling, faction standing, stolen-goods penalties, theft
detection, uniforms, illegal-goods, or other `RClickAutoTrade` rules. Documents
and receipts must call it simplified project-owned pricing, not Kenshi
adjudication.

An item cell's `item_base_value` and `item_sell_value` are the two values
reported by `Item::getValueSingle`; neither field alone proves the final charge.
Later inventory and money telemetry is the outcome evidence.

## Body shift semantics

`shift_into_body` is elective, not total-loss-only. It resolves one exact
conscious, non-animal, non-hostile body; uses `PlayerInterface::recruit` when
crossing into the player faction; creates a separate squad; carries selection
identity across `Character::setFaction`; makes that squad current; and selects
and tracks the body. The exact implementation and open live-proof obligations
are recorded in the
[body-shift record](../../docs/KENSHI_BODY_SHIFT_PLAN.md).

## Build and install

The plug-in targets Visual C++ 2010 SP1 x64 (`v100`) and the configured
maintained KenshiLib headers/libraries.

1. Set `KENSHILIB_DIR` to the dependency directory containing `Include` and
   `Libraries`.
2. Set `BOOST_INCLUDE_PATH` to Boost 1.60 containing `boost` and `stage\lib`.
3. Run `scripts\native_doctor.ps1` and resolve every failed check.
4. Run `scripts\build_native.ps1`. The Release x64 build also runs
   `NativeCommandProtocolTests.exe` against `tests\fixtures\native_commands`.
5. Run `scripts\stage_native.ps1 -BuiltDll <path-to-built-dll>`.
6. After review, copy the staged `KenshiAgentTelemetry` folder into Kenshi's
   `mods` directory and enable it in the launcher.

The staging script includes the DLL, `RE_Kenshi.json`, third-party notices,
this README, and the 46-byte native-only `.mod` stub. It stages only; it does
not install into Kenshi.

## Output paths

By default the plug-in writes:

```text
%LOCALAPPDATA%\KenshiAgent\telemetry.latest.json
%LOCALAPPDATA%\KenshiAgent\plugin_status.json
%LOCALAPPDATA%\KenshiAgent\native_command.request.json
```

Set `KENSHI_AGENT_TELEMETRY_DIR` before launching Kenshi to override the folder.
The parent of an override must already exist; the plug-in creates only the final
folder component.

## Evidence classification

### Source-proven

- The hook, protocol, command, inventory-model, simplified-pricing, and body
  shift paths above are present in the current source and in the configured
  KenshiLib declarations they call.
- Protocol 1.18.0 and the 43-entry loaded-world capability manifest are embedded
  into the native build inputs.
- The file-change watcher and the optional hotkey both dispatch through the same
  request parser and command handler.

### Test-proven

- Portable tests validate the strict Python request/acknowledgement models,
  shared command vocabulary, generated schemas, capability manifest/header
  parity, and golden fixture set.
- A Windows native build runs the production C++ parser/serializer against the
  same golden request fixtures.
- `scripts/check_native_provenance.py` compares protocol and capability strings
  in the installed binary and records built/installed hashes. See the current
  [checkpoint](../../docs/CHECKPOINT.md) for the measured result.

### Live-proven

Named live evidence is operation-specific and belongs in
`docs/reconstruction/interaction_proof_status.json`. Representative accepted
commands include trade-window opening, inventory-model transfers, resource
production, and character orders. Each live conclusion depends on later engine
telemetry in its named bundle, not merely on request delivery or an
acknowledgement.

### Withheld and open

- The plug-in does not support plural simultaneously active native command
  records.
- Several group-recipient, delayed-continuation, session-reset, and retained
  order lifecycle conclusions remain unproven.
- The native recovery close command is not a planner-visible general close
  operation.
- Body shift lacks a complete named operation bundle even though a manual live
  dispatch informed the implementation.
- Alternate host configurations and long-duration stability require their own
  evidence; an installed hash match does not prove live behavior.

Exact current source, test, live, and withheld classifications are maintained in
the [checkpoint](../../docs/CHECKPOINT.md) and
[proof ledger](../../docs/reconstruction/interaction_proof_status.json).
