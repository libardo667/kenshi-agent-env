# Guide: running against real Kenshi

Per-run evidence lives in its commit and `runs/<run-id>/`; this is procedure only.

## Acknowledgements are separate on purpose

None of these implies another. Pass only what the run actually needs:

| Flag | Authorizes |
| --- | --- |
| `--execute-live-actions` | sending real input at all |
| `--acknowledge-native-assisted-control` | native-assisted profiles; never use it for an interface-only evidence run |
| `--acknowledge-continuous-live` | a live continuous profile |
| `--exclusive-input-session` | the human hands over the whole desktop |

`--exclusive-input-session` keeps Kenshi foreground and the guest cursor in place.
Omit it to have each polite lease restore the prior foreground and cursor.

F12 disarms automatic takeover for the remainder of the run.

## One supported entrypoint

From WSL, use `./dev`; it selects the prepared Windows Python, translates
configuration and planner-script paths once, and invokes the checked-in live
launcher without a pseudo-terminal:

```bash
./dev launch --preflight-only
./dev launch
./dev play --game-start kae-01-funded-solo \
  --campaign fresh-funded-solo --steps 80 \
  --continuous --execute --native-assisted \
  --acknowledge-continuous-live --exclusive
./dev recover
./dev crash
./dev crash --dismiss
```

`play` performs guarded launch/load, then immediately invokes `journey` with the
same options; it does not imply any acknowledgement. Both default to
`config/live.longform.yaml`; pass a campaign or attested scenario. Other commands
default to `config/live.burnin.yaml`. Never substitute direct Windows-Python,
native-file, input-snippet, or PTY workarounds.

The live profiles require the checked-in 30 fps renderer profile and an active
1920x1080 external display. Actual `launch` and executing `journey` commands
switch to external-only mode, verify the laptop panel is off, and restore
extended mode on every handled exit. Any nonzero or interrupted journey invokes
`./dev recover`: it causally pauses a loaded world, dismisses only exact owned
inventories after any active native command terminates, leaves Kenshi open, and
restores a stranded display lease. Run it directly after interrupted cleanup. A power loss may still require `Win+P`,
then **Extend**. The ownership overlay remains opt-in.

If preflight reports a terminal crash, `./dev crash` first archives the newest
dump plus current logs, telemetry, settings, and frame under `runs/crashes/`.
`--dismiss` is explicit because it closes an unsent report; it archives first,
dismisses each exact terminal layer with bounded ordinary input, aborts on human
input, and never force-terminates a process that fails to exit.
Human keyboard or mouse input during startup cancels the pending primitive and
yields control. After three quiet seconds, the same visible five-second
takeover countdown used by a journey begins; new input resets it and F12
permanently disarms startup automation. Startup timeouts do not elapse while
the human owns input. `./dev launch --resume-launcher` remains available for an
exact small pre-game window left by an older guarded interruption.

## Before a run

- Toolchain and host versions match [`GUIDE_UPSTREAM_LOCK.md`](GUIDE_UPSTREAM_LOCK.md).
- Plugin built Release x64 on the v100 toolset, loads without a log error, and
  `plugin_status.json` reaches `ready`.
- Telemetry parses against its schema, `sequence` advances at roughly 2 Hz, and
  the capture timestamp is fresh UTC.
- Screenshot dimensions match calibration, and the window filter matches only
  Kenshi.
- Code is frozen. Do not edit the tree while a run is in flight.

## Standing verification items

Record evidence for each. **Do not mark one complete from code inspection.**

- [ ] Uninstalling the plugin returns Kenshi to its prior behavior.
- [ ] Pause and speed changes match the UI over at least 20 trials each.
- [ ] Squad count and names match after recruit, dismiss, reorder, KO, and death.
- [ ] Character position moves plausibly and does not jump on zone transitions.
- [ ] Save/load and returning to the title screen neither crash nor retain stale
      pointers.
- [ ] Controller and Kenshi run at equal integrity levels.
- [ ] F12 prevents the next primitive action.
- [ ] Human input during launch cancels the pending primitive, waits for the
      configured quiet interval and takeover countdown, revalidates the current
      semantic startup state, and only then continues; F12 disarms it.
- [ ] At 1920x1080 and one alternate client size the launcher advances by Enter,
      RE_Kenshi does not open its settings panel, and exact current
      `Continue`/save controls load without a fixed startup coordinate.
- [ ] Human input during a continuous run raises a `human_control` banner and a
      confirmed pause; after three quiet seconds a five-second takeover
      countdown appears, new input resets it, F12 disarms it, and a completed
      countdown causes a fresh replan rather than resuming the cancelled plan.
- [ ] One calibrated click survives 50 repeated trials without drift.
- [ ] Loss of foreground focus aborts safely.
- [ ] A failed action is never reported successful before observation.
- [ ] Stale telemetry triggers pause or stop.
- [ ] Every episode produces a complete JSONL log and final summary.

## Host stability

A live run is only as stable as the GPU driver. This host has produced Windows
`LiveKernelEvent 141` display-driver timeouts on both tested Intel Iris Xe
drivers, including recovered hangs that fresh telemetry alone would miss.
Launch health and journeys therefore reject any new Event 141 after their
baseline; crash archives include the matching WER metadata and watchdog-dump
identity when Windows exposes them. A clean soak proves only its scene; a quiet
paused town does not clear water- or effects-heavy locations.

## What live evidence does and does not cover

Live proofs here are single supervised runs on one host and save; they show a path can work, not that it generalizes. A completed
`move_in_direction` smoke does not clear every local route; one Storm House exit
does not clear every building. Treat each as one data point and say so when
citing it.
