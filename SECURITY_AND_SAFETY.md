# Security and operational safety

This project controls a foreground desktop application. Treat it like a robot
with access to your keyboard and mouse, not like a harmless text script.

Live actions require both `safety.live_actions_enabled: true` in configuration
and the CLI flag `--execute-live-actions`. Dry-run is the default. F12 is the
default emergency-stop key and is checked before every primitive action.

`interface_only` is the default control mode. It removes `control.*`
capabilities and native command acknowledgement state from planner
observations, omits marked native-assisted skills, and rejects those skills in
both `ActionGuard` and `LiveEnvironment`.

`native_assisted` permits only skills explicitly marked
`requires_native_assisted`. Executing that mode requires
`control.native_assisted_actions_enabled: true` and
`--acknowledge-native-assisted-control` in addition to the normal two
live-action gates. Logs, receipts, overlays, and summaries carry the mode.

The Windows controller uses a polite input lease by default. It waits for a
configurable idle interval before capture or input, records foreground/cursor
state, and Alt+Tabs away from Kenshi before restoring the cursor after actions.
Resumed human input interrupts a
movement pulse; safety re-pause is the only operation allowed to reclaim Kenshi
focus before the controller yields it back. After the next quiet interval the
agent observes and replans rather than replaying an interrupted intent.

Run Kenshi and the controller at the same Windows integrity level. Do not run
one as administrator and the other normally. Keep the Kenshi window title
filter narrow. Close applications containing secrets before live tests. Start
with a disposable save and a fixed resolution/UI scale.

The plugin's telemetry path is observational, but the DLL is not globally
read-only: in native-assisted mode its bounded vendor bridge may issue a
`PLAYER_TALK_TO` player order. No mode permits health, position, money, faction,
save/load, or arbitrary task mutation. Interface-only actions remain visible
keyboard/mouse operations through the ordinary UI.

Do not read Kenshi or MyGUI object state from a worker thread. Sample game state
on a known game/UI thread, copy it into plain data, and only then hand it to
other threads or files. Use atomic replace for telemetry so the Python reader
never consumes a half-written snapshot.

Store API keys in environment variables. Session logs may contain screenshots,
character names, prompts, and model outputs; do not publish them blindly.
