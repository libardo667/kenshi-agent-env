# Live validation checklist

Record evidence for every item. Do not mark an item complete based on code
inspection alone.

## Native loading

- [x] Exact Kenshi executable version recorded.
- [x] Exact RE_Kenshi release recorded.
- [x] Exact KenshiLib dependency commit or release recorded.
- [x] Plugin built Release x64 with the required v100 toolset.
- [x] Plugin loads without an error in the RE_Kenshi/Kenshi logs.
- [x] `plugin_status.json` reaches `ready`.
- [ ] Uninstalling the plugin returns Kenshi to its prior behavior.

## Telemetry

- [x] Snapshot parses against `schemas/telemetry.schema.json`.
- [x] Sequence increases at approximately two hertz.
- [x] Capture timestamp is UTC and fresh.
- [ ] Pause toggles match the UI for at least 20 trials.
- [ ] Speed changes match the UI for at least 20 trials.
- [ ] Squad count and names match after recruit, dismiss, reorder, KO, and death.
- [ ] Selection identity follows portrait selection.
- [ ] Character position moves plausibly and does not jump on zone transitions.
- [ ] Saving/loading and returning to the title screen do not crash or retain
      stale pointers.
- [x] Missing capabilities remain absent rather than fabricated.

### Evidence from 2026-07-22 smoke test

- RE_Kenshi logged `KenshiAgentTelemetry -> KenshiAgentTelemetry.dll`, then the
  plugin logged `telemetry hook installed` without a plugin error.
- A snapshot parsed through `TelemetrySnapshot`; sequence advanced from 23 to
  31 in four seconds and its UTC timestamps advanced with it.
- The disposable Wanderer `Sand` appeared as selected with 1,000 cats. A manual
  move produced movement speed 53.15 and a 7.89-unit position change. A manual
  pause produced `paused: true` and speed multiplier 0.
- The position check remains open because zone transitions were not tested.
- KenshiLib's raw `isGettingEaten` byte was true on the healthy new character.
  That unvalidated field was removed, a warning now names the omission, and a
  clean rebuild/load confirmed it absent.
- A zero-byte `.mod` marker was rejected as invalid and produced the first-run
  crash. The staging script now emits the exact 46-byte stub shared by current
  upstream examples, guarded by a hash regression test. The corrected run
  loaded cleanly.

## Capture and input

- [ ] Window title filter identifies only Kenshi.
- [ ] Client screenshot excludes unrelated desktop applications.
- [ ] Screenshot dimensions match calibration.
- [ ] Controller and Kenshi run at equal integrity levels.
- [ ] F12 prevents the next primitive action.
- [ ] Dry-run logs proposed actions without sending input.
- [ ] One key action works in a disposable save.
- [ ] One calibrated click works for 50 repeated trials without drift.
- [ ] Loss of foreground focus aborts safely.

## Agent loop

- [ ] Planner receives fresh telemetry and the matching screenshot.
- [ ] Planner output validates without repair.
- [ ] A failed action is not reported as successful before observation.
- [ ] Duplicate failed procedures are detected.
- [ ] Stale telemetry triggers pause or stop.
- [ ] Every episode produces a complete JSONL log and final summary.
