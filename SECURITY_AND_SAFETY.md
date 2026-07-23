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

Stable native entity IDs contain no process pointer and are scoped to an
explicit process/session generation. Display names remain descriptive only.
Any session change or target omission invalidates target-bound work. The native
vendor bridge additionally requires a globally unique caller command ID, exact
based-on telemetry revision, `native_assisted` mode, current identity session,
one exact selected character, and one exact role-confirmed target. Python waits
only for that command's acknowledgement on a later snapshot. The plugin retains
at most 16 keyed acknowledgements, never reissues a duplicate ID, cancels on
selection or target-lifetime/role change, and completes only for dialogue bound
to the exact target. Rejection is definitive and does not start a movement
pulse; timeout or transport failure remains uncertain and is never retried
automatically.

Continuous mode is still blocked for live-labeled environments. In portable
continuous runs, one observation pump feeds an authoritative bounded store.
State-changing plan actions receive a command ID and start/completion revision;
unchanged, regressing, or conflicting state cannot certify progress. Missing
nearby capability does not become evidence that an entity disappeared.

An independent portable supervisor subscribes to that stream and can cancel a
blocked planner or plan action without waiting for the strategic loop. It
reacts to deterministic reflexes, stale/stalled telemetry, pause-capability
withdrawal, an exact `human_input_detected` stream event, and unauthorized
unpause. A canceled in-flight action is treated as possibly delivered: its
budget stays spent and its command outcome is inconclusive.

The only cleanup exception is `PauseAction(paused=true)`. It still requires the
configured action allowlist and matching control mode, and it never permits an
unpause; it bypasses only the per-minute rate counter so prior activity cannot
lock out the safest local action. Cleanup is not reported successful until a
causally later revision with `game.pause` capability confirms `paused=true`.
Failure, missing capability, or timeout remains explicit. This has portable
fake-environment evidence only: live continuous mode is blocked, so there is no
claim yet about concurrent F12, human-input, or Windows-controller latency.

Portable configured movement pulses expose an executor-owned option lifecycle.
A concurrent planner sees an immutable active-plan snapshot and has advisory
authority over future steps only. Its output cannot alter the running movement,
restart an active/completed step, or execute until it matches the original
plan/version/revision and passes a second post-movement validation against
latest state and remaining budgets. This does not replace the live movement
pulse's own re-pause guarantee, and live continuous mode remains blocked.

Do not read Kenshi or MyGUI object state from a worker thread. Sample game state
on a known game/UI thread, copy it into plain data, and only then hand it to
other threads or files. Use atomic replace for telemetry so the Python reader
never consumes a half-written snapshot.

Store API keys in environment variables. Session logs may contain screenshots,
character names, prompts, and model outputs; do not publish them blindly.
