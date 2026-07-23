# Kenshi control and plugin research

This note records the control references and open-source plugin patterns most
useful to the live agent. The shallow reference clones live outside this repo at
`/home/levib/projects/personal-projects/kenshi-reference-plugins/`; they are
research inputs, not vendored dependencies.

## Control authority

The installed game keymap is the best authority for this machine:

`C:\Program Files (x86)\Steam\steamapps\common\Kenshi\controls.cfg`

It currently binds pause to Space, character slots to `1` through `0`, normal
through maximum game speeds to `F2` through `F4`, camera movement to WASD,
camera rotation to Q/E, character focus to F, world zoom to Home/End, map to M,
and inventory to I. The agent configuration should follow this file instead of
assuming Kenshi defaults, because all bindings are user-remappable.

The community [Kenshi controls reference](https://kenshi.wiki/index.php?title=Controls)
also documents portrait selection and centering, number-key character selection,
camera controls, and contextual right-click behavior. The
[Steam beginner guide](https://steamcommunity.com/sharedfiles/filedetails/?id=1315268819)
is especially useful for interaction semantics: a right-click issues the
contextual default action, while right-click-and-hold exposes the interaction
menu. That distinction matters because a blind default action can be talk,
move, operate, steal, or attack.

Observed behavior still outranks a receipt saying input was sent. In this live
build, absolute Windows cursor positioning did not move Kenshi's drawn cursor,
raw relative movement did, and a scan-code Space request failed to pause even
though the installed binding is Space. Clicking the visible pause transport via
relative input produced the `PAUSED` banner and is the proven fallback.
Live calibration subsequently established that the bottom HUD has distinct
Play and Pause buttons, not a toggle: Play resumes at normalized
`(0.792, 0.723)`, while Pause stops at `(0.765, 0.723)` in the current 1920x1080
client. Runtime movement therefore uses separate state-specific controls.

## Reference repositories

### Direct Control

[smokefoolius/Kenshi-Direct-Control](https://github.com/smokefoolius/Kenshi-Direct-Control)
is the most immediately useful control reference. It:

- reads the selected character from
  `ou->player->selectedCharacter.getCharacter()`;
- hooks `PlayerInterface::playerControl` and per-frame movement processing;
- checks `ForgottenGUI::isAnyInventoryWindowOpen()` plus the concrete player,
  NPC, trader, and trade inventory windows;
- hooks `ForgottenGUI::showTradeWindow` at the interaction edge, before the
  trade UI becomes visibly open; and
- suspends direct movement during loot/trade UI and save-load transitions.

For this agent, those patterns suggest native selected-character telemetry,
native trade/dialogue state, load guards, and UI-aware action suspension before
we attempt more screen-coordinate heuristics.

### RTS Control Groups

[Mineomi/RTSControlGroups](https://github.com/Mineomi/RTSControlGroups) is a
small, clear selection example. It uses
`PlayerInterface::getAllSelectedObjects`, `selectObject`, and
`focusCameraSelectedCharacter`, hooked from `GameWorld::processKeys`. This is a
direct recipe for native `select_lekko` and `focus_selected` commands.

### KenshiLua and KenshiPy

[Genpretz/KenshiLua](https://github.com/Genpretz/KenshiLua) and
[Genpretz/KenshiPy](https://github.com/Genpretz/KenshiPy) demonstrate that a
runtime scripting bridge is viable. Their exposed API includes:

- `GameWorld::userPause`, `isPaused`, and game-speed methods;
- `InputHandler::sendEvent`, key events, and binding inspection;
- `PlayerInterface` selected-character state;
- inventory item counts, item functions, values, money, and GUI refresh;
- shop trader and trader-inventory types; and
- frame, key, dialogue, and character-selection callbacks.

KenshiLua is the stronger current reference because its hand-written bindings
cover more of KenshiLib and avoid KenshiPy's Python 3.4/SWIG constraints. It is
still a research source rather than a dependency: our existing native telemetry
plugin can expose a much smaller JSON command surface without embedding a second
language runtime.

The class split is important for target selection. `Character::isATrader()` is
not an exact-person signal: live telemetry marked caravan members, guards, and
animals alike when their platoon traded. KenshiLib separately exposes
`ActivePlatoon::getIsTrader()`, `getHasVendorList()`, `getSquadLeader()`, and
`Character::hasDialogue()`. Most decisively, `ShopTrader::getTrader()` returns
the exact `Character*` that owns its inventory.

Live validation ruled out a seemingly direct lookup:
`GameWorld::getObjectsWithinSphere(..., SHOP_TRADER_CLASS, ...)` returned zero
objects with the Hub Barman nearby. Disassembly of Kenshi 1.0.65 confirmed that
`InventoryManager` is not a registry either: the singleton is eight bytes and
holds one transient `ShopTrader*`. The plugin therefore hooks `ShopTrader`
construction and destruction before save load, maintains a bounded live
registry, and matches its owner pointers against nearby characters. For
pre-click validity it also asks Kenshi directly through
`getPlayerTaskProbability(PLAYER_TALK_TO, target, probability)`.

The headers also expose the exact player-order seam needed for an occluded
indoor vendor:
`PlayerInterface::newPlayerTaskSelectedCharacters(PLAYER_TALK_TO, handle,
destinationIndoors, position, false)`. The live plugin now invokes that method
only after repeating its humanoid, vendor-list, leader, dialogue, conscious,
and non-hostile checks. In the Hub proof, Kenshi pathfinding reduced the Barman
distance by 146.60 units in one two-second pulse and opened dialogue on the
next.

### Inventory and trade implementations

[XxAtreuSSxX/BetterLooting](https://github.com/XxAtreuSSxX/BetterLooting)
contains extensive `InventoryGUI`, inventory-section, item-function, placement,
and transfer logic, including explicit food classification and provenance
guards. It is useful when implementing inventory observation or a guarded item
transfer.

[XxAtreuSSxX/BetterSellPrices](https://github.com/XxAtreuSSxX/BetterSellPrices)
demonstrates trade-price and tooltip interception. Some of it relies on raw
version-specific RVAs, so prefer version-independent KenshiLib methods where
available and isolate any unavoidable RVA behind a strict version check.

### Examples and offline API map

[BFrizzleFoShizzle/KenshiLib_Examples](https://github.com/BFrizzleFoShizzle/KenshiLib_Examples)
provides minimal hook, dialogue, UI-button, export, and import examples.
[ReKenshi_KenshiLib_Swagger](https://github.com/regulareverydaynormalmazaf/ReKenshi_KenshiLib_Swagger)
provides an offline symbol and relationship map for finding reconstructed
classes, methods, globals, and real plugin examples.

## Implemented command seam and next architecture

The first high-leverage native control is now implemented beside the telemetry
writer. A private input chord is observed by the `PlayerInterface::update` hook,
validated and executed on Kenshi's main/UI thread, and acknowledged in
`native_control` telemetry. Its only command is the deliberately narrow
`approach_confirmed_vendor`.

Further commands should preserve the same shape:

1. `set_pause(expected_current, desired)` using `GameWorld::userPause`;
2. `select_character(expected_name)` and `focus_selected` using
   `PlayerInterface` methods;
3. read-only `ui_state` fields for dialogue, inventory, and trade windows;
4. additional native movement orders with bounded destinations and pause
   envelopes;
5. only after observation is trustworthy, a one-item guarded trade operation.

This preserves screenshots as perceptual evidence while moving brittle,
high-consequence operations—pause, selection, focus, UI-state detection, and
eventually purchase—onto version-independent game APIs. Any overlay or decision
stream must be created and updated only on the UI thread, matching KenshiLib's
thread-safety guidance.

All cloned plugin sources are GPLv3 or should be treated as reference-only until
their licenses are verified. This project already uses KenshiLib and should keep
its GPL obligations explicit when code is adapted rather than merely studied.
