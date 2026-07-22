# Live validation checklist

Record evidence for every item. Do not mark an item complete based on code
inspection alone.

## Native loading

- [ ] Exact Kenshi executable version recorded.
- [x] Exact RE_Kenshi release recorded.
- [x] Exact KenshiLib dependency commit or release recorded.
- [x] Plugin built Release x64 with the required v100 toolset.
- [ ] Plugin loads without an error in the RE_Kenshi/Kenshi logs.
- [ ] `plugin_status.json` reaches `ready`.
- [ ] Uninstalling the plugin returns Kenshi to its prior behavior.

## Telemetry

- [ ] Snapshot parses against `schemas/telemetry.schema.json`.
- [ ] Sequence increases at approximately two hertz.
- [ ] Capture timestamp is UTC and fresh.
- [ ] Pause toggles match the UI for at least 20 trials.
- [ ] Speed changes match the UI for at least 20 trials.
- [ ] Squad count and names match after recruit, dismiss, reorder, KO, and death.
- [ ] Selection identity follows portrait selection.
- [ ] Character position moves plausibly and does not jump on zone transitions.
- [ ] Saving/loading and returning to the title screen do not crash or retain
      stale pointers.
- [ ] Missing capabilities remain absent rather than fabricated.

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
