# Security and operational safety

This project controls a foreground desktop application. Treat it like a robot
with access to your keyboard and mouse, not like a harmless text script.

Live actions require both `safety.live_actions_enabled: true` in configuration
and the CLI flag `--execute-live-actions`. Dry-run is the default. F12 is the
default emergency-stop key and is checked before every primitive action.

Run Kenshi and the controller at the same Windows integrity level. Do not run
one as administrator and the other normally. Keep the Kenshi window title
filter narrow. Close applications containing secrets before live tests. Start
with a disposable save and a fixed resolution/UI scale.

The native plugin is read-only by design. It may inspect game objects and write
telemetry, but it must never issue player orders, alter health, teleport units,
change money, modify factions, or invoke save/load. All player actions must be
visible keyboard/mouse operations through the ordinary interface.

Do not read Kenshi or MyGUI object state from a worker thread. Sample game state
on a known game/UI thread, copy it into plain data, and only then hand it to
other threads or files. Use atomic replace for telemetry so the Python reader
never consumes a half-written snapshot.

Store API keys in environment variables. Session logs may contain screenshots,
character names, prompts, and model outputs; do not publish them blindly.
