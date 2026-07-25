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
configurable idle interval before capture or input and records the foreground
and cursor state. In ordinary absolute-pointer mode it can Alt+Tab away before
restoring the cursor. In Kenshi's relative-pointer mode the OS and game cursors
must be synchronized from a known corner and the final cursor is deliberately
left in place; an absolute restore would desynchronize them and turn the next
small human movement into an edge jump. Resumed human input cancels the active
plan and yields control. After the next authorized quiet interval the agent
observes and replans rather than replaying interrupted intent.

The `./dev launch` path also uses a bounded input lease. Any new human input is
terminal for that launch attempt: it emits no further input and does not retry
title-screen clicks. Startup selects exact live labels and current bounds, so it
does not inherit gameplay coordinates. Each bounded startup input restores or
hands back foreground/cursor state according to the active pointer mode.
Profile-calibrated in-game pointer actions require the exact configured client
size; semantic-current actions instead rebind current UI bounds. Both repeat
their relevant checks after the input lease is acquired and immediately before
dispatch.

Because the lease wait is unbounded by design, a continuous plan step also
carries a bounded `ExecutionToken` into dispatch. Inside the acquired lease,
after the calibration recheck and immediately before the first primitive, the
environment re-reads the latest canonical revision and re-evaluates that step's
plan assumptions and typed preconditions, its control mode, and current human
input/emergency-stop evidence. A regressed revision, changed control mode,
withdrawn capability, human input, emergency stop, or any assumption or
precondition that is no longer `true` emits zero input and returns an explicit
`InputBoundaryRejected` receipt. `unknown`, `unavailable`, and `stale` block
input exactly as `false` does. This window is never closed by shortening the
lease timeout or disabling polite handoff.

A profile-calibrated pointer action additionally requires a matching
calibration identity. Each action is classified as coordinate-independent,
semantic-current (its target resolved from live bounds re-read inside the
lease), profile-calibrated, or unsupported. A profile-calibrated action
compares every calibration field the profile declares — client size, and where
declared window mode, UI scale, DPI transform, keymap, and profile/macro hashes
— against what the controller can observe. A declared field the host cannot
read is `unknown` and blocks input; it is never assumed to match. A mismatch or
`unknown` inside the lease is caught by the same boundary fence. The controller
today observes only client width and height, so only those may be declared in a
live profile until the remaining fields have real observation support.

Run Kenshi and the controller at the same Windows integrity level. Do not run
one as administrator and the other normally. Keep the Kenshi window title
filter narrow. Close applications containing secrets before live tests. Start
with a disposable save and a fixed resolution/UI scale.

The plugin's telemetry path is observational, but the DLL is not globally
read-only. In native-assisted mode its bounded bridge may issue one of three
declared player orders: talk to an exact valid dialogue target, walk to an exact
nearby character, or issue a bounded bearing/distance walk. The third path has
portable and native-build proof but still awaits a live Kenshi command smoke.
No mode permits direct
health, position, money, faction, save/load, editor, or arbitrary task mutation.
Interface-only actions remain visible keyboard/mouse operations through the
ordinary UI.

Stable native entity IDs contain no process pointer and are scoped to an
explicit process/session generation. Display names remain descriptive only.
Any session change or target omission invalidates target-bound work. Every
native request additionally requires a globally unique caller command ID, exact
issue-time telemetry revision, `native_assisted` mode, current identity session,
and exactly one selected character. Targeted requests bind one exact current
stable ID; the directional model instead binds bounded numeric fields and an
empty target. Protocol 0.6.0 enforces those command-specific identities in both
Python and the production C++ parser/serializer, with one keyed option owning
the direction through terminal acknowledgement. Directional movement remains
an unproven live path, not a reviewed live guarantee. For every command, Python
waits only for that command's
acknowledgement on a later snapshot. The plugin retains at most 16 keyed
acknowledgements, never reissues a duplicate ID, cancels on selection, pause,
or target-lifetime/role change, and uses command-specific completion: exact
dialogue for approach, bounded arrival for walking.
Rejection is definitive; timeout or transport failure remains uncertain and is
never retried automatically.

Live continuous mode remains unavailable unless
`planning.live_execution_policy` names an implemented policy and the CLI
receives `--acknowledge-continuous-live`. The implemented generic policy is
`dialogue_interaction_v1`; its historical name does not restrict it to
dialogue. It accepts only planner-visible contracted actions and run control,
never raw controller primitives. In continuous runs, one observation pump feeds
an authoritative bounded store. State-changing plan actions receive a command
ID and start/completion revision; unchanged, regressing, or conflicting state
cannot certify progress. Missing nearby capability does not become evidence
that an entity disappeared.

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
Failure, missing capability, or timeout remains explicit. Portable tests cover
the complete cancellation/cleanup matrix; supervised live runs additionally
proved human-input handback and confirmed pause. Broader repeated F12,
focus-loss, and controller-latency trials remain part of the live checklist.

Two current policy gaps must remain explicit:

- `allow_live_unpause_actions=false` is enforced only for direct
  `PauseAction(paused=false)`. The allowlisted
  `UseGameBindingAction(binding=pause)` can still toggle an unpaused game.
- The verified cleanup above belongs to supervisor preemption. Normal stop,
  budget/replan exhaustion, cancellation, exception, and objective-completion
  exits do not run one shared final-state policy; `LiveEnvironment.close()` is
  intentionally a no-op.

Likewise, requiring a success condition on a causally later revision does not
make that condition an authoritative action effect. Many current conditions are
planner-authored and contracts generally do not derive an operator, expected
value, and baseline from the bound pre-action state. Later correlated state can
still produce a false semantic success.

Configured movement and semantic approach expose executor-owned option
lifecycles. A concurrent planner sees an immutable active-plan snapshot and has
advisory authority over future steps only. Its output cannot alter running
movement, restart an active/completed step, or execute until it matches the
original plan/version/revision and passes a second post-movement validation
against latest state and remaining budgets. Stop-motion profiles retain bounded
re-pause behavior; the long-form profile instead monitors ordinary movement in
an intentionally running world.

Do not read Kenshi or MyGUI object state from a worker thread. Sample game state
on a known game/UI thread, copy it into plain data, and only then hand it to
other threads or files. Use atomic replace for telemetry so the Python reader
never consumes a half-written snapshot.

Store API keys in environment variables. Session logs may contain screenshots,
character names, prompts, and model outputs; do not publish them blindly.
