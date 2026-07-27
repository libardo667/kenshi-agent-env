# Security and operational safety

This project controls a foreground desktop application. Treat it like a robot
with access to your keyboard and mouse, not a harmless text script.

## Gates

Live actions require both `safety.live_actions_enabled: true` and
`--execute-live-actions`. Dry-run is the default. F12 is the emergency stop and
is checked before every primitive.

`interface_only` is the default control mode: it strips `control.*` capabilities
and native acknowledgement state from planner observations, omits native-assisted
skills, and rejects them again in both `ActionGuard` and `LiveEnvironment`.

`native_assisted` permits only skills marked `requires_native_assisted`, and
additionally requires `control.native_assisted_actions_enabled: true` and
`--acknowledge-native-assisted-control`. Live continuous mode additionally
requires an implemented `planning.live_execution_policy` and
`--acknowledge-continuous-live`. Logs, receipts, overlays, and summaries carry
the mode so evidence cannot be conflated.

Run Kenshi and the controller at the same Windows integrity level — never one as
administrator and the other not. Keep the window-title filter narrow, close
applications holding secrets before live tests, and start from a disposable save
at a fixed resolution. Store API keys in environment variables. Session logs may
contain screenshots, character names, prompts, and model outputs; do not publish
them blindly.

## Input ownership

The Windows controller uses a polite input lease: it waits for a configured idle
interval before capture or input and records foreground and cursor state. In
absolute-pointer mode it can Alt+Tab away before restoring the cursor. In
Kenshi's relative-pointer mode the OS and game cursors are synchronized from a
known corner and the final cursor is deliberately left in place — an absolute
restore would desynchronize them and turn the next small human movement into an
edge jump.

Resumed human input cancels the active plan and yields control; after the next
authorized quiet interval the agent observes and replans rather than replaying
interrupted intent. The `./dev launch` path uses the same bounded lease, and any
new human input is terminal for that launch attempt.

Because the lease wait is unbounded by design, every ordinary planner-authored
live action in both schedulers carries a bounded `ExecutionToken` into dispatch.
Details in [ADR_INPUT_BOUNDARY_AUTHORITY_V2](docs/ADR_INPUT_BOUNDARY_AUTHORITY_V2.md) and
[ADR_CALIBRATION_IDENTITY](docs/ADR_CALIBRATION_IDENTITY.md); the operator-facing
guarantee is that stale telemetry, a regressed revision, changed control mode,
withdrawn capability or reference, human input, emergency stop, calibration
mismatch, or any plan condition no longer `true` emits **zero input** and returns
an explicit `InputBoundaryRejected` receipt. `unknown`, `unavailable`, and
`stale` block input exactly as `false` does. This window is never closed by
shortening the lease timeout or disabling polite handoff.

Global rate and purchase authority follows the same delivery verdict as plan
risk: a command-matched zero-input rejection releases both reservations;
accepted or ambiguous delivery commits both.

The supported `./dev close` path owns pause-before-close: an unpaused loaded
world must advance to a causally confirmed pause before `WM_CLOSE`. It then
re-reads telemetry and still refuses an active native command, modal, or
dialogue. Failure never falls through to force termination or ad-hoc input.

## What the native bridge may do

The telemetry path is observational, but the DLL is not globally read-only. In
native-assisted mode the bounded bridge may issue one of seven declared player
orders: talk to an exact valid dialogue target; walk to an exact nearby
character; issue a bounded bearing/distance walk; resolve and use the selected
character's current unlocked building exit; or operate an exact current natural
resource through task-start or output-ready semantics; or open that exact
resource's ordinary inventory UI. **No mode permits direct health, position,
money, faction, save/load, editor, or arbitrary task mutation.** Interface-only
actions remain visible keyboard and mouse operations through the ordinary UI.

Every native request requires a globally unique caller command ID, exact
issue-time revision, `native_assisted` mode, the current identity session, and
exactly one selected character. Stable IDs contain no process pointer and are
scoped to a process/session generation; any session change or target omission
invalidates target-bound work. Python waits only for that command's own
acknowledgement on a later snapshot. Rejection is definitive; timeout or
transport failure stays uncertain and is **never** retried automatically. See
[ADR_CAUSAL_NATIVE_COMMANDS](docs/ADR_CAUSAL_NATIVE_COMMANDS.md) and
[ADR_STABLE_NATIVE_IDENTITY](docs/ADR_STABLE_NATIVE_IDENTITY.md).

A shared continuous-unpaused no-progress timer terminally cancels blocked native
movement rather than leaving the bridge poisoned.

## Independent supervision

A portable supervisor subscribes to the world-state stream and can cancel a
blocked planner or plan action without waiting for the strategic loop, reacting
to deterministic reflexes, stale or stalled telemetry, pause-capability
withdrawal, an exact `human_input_detected` event, and unauthorized unpause. A
cancelled in-flight action is treated as possibly delivered: its budget stays
spent and its command outcome is inconclusive.

The only cleanup exception is `PauseAction(paused=true)`. It still requires the
allowlist and matching control mode and never permits an unpause; it bypasses
only the per-minute rate counter, so prior activity cannot lock out the safest
local action. Cleanup is not successful until a causally later revision with
`game.pause` capability confirms `paused=true`.

## Explicit gaps

- Requiring a success condition on a causally later revision does not make it an
  authoritative effect. Most conditions are planner-authored and do not derive an
  operator, expected value, and baseline from bound pre-action state, so later
  correlated state can still produce a false semantic success. Only
  `controller_verified` contracts avoid this.
- Live evidence is single supervised runs on one host and save. One directional
  probe does not generalize every bearing, obstacle, or scene.

## Threading

Never read Kenshi or MyGUI state from a worker thread. Sample on a known game/UI
thread, copy into plain data, then hand it to other threads or files. Telemetry
uses atomic replace so the reader never consumes a half-written snapshot.
