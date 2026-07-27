# Guide: running against real Kenshi

Per-run evidence lives in the commit that landed the capability and in
`runs/<run-id>/`. This guide is the standing procedure only.

## Acknowledgements are separate on purpose

None of these implies another. Pass only what the run actually needs:

| Flag | Authorizes |
| --- | --- |
| `--execute-live-actions` | sending real input at all |
| `--acknowledge-native-assisted-control` | native-assisted profiles; never use it for an interface-only evidence run |
| `--acknowledge-continuous-live` | a live continuous profile |
| `--exclusive-input-session` | the human hands over the whole desktop |

`--exclusive-input-session` keeps Kenshi foreground and leaves the guest cursor
in place so a single-display run stays observable. Omit it on a shared machine:
the polite input lease then waits for idle input and restores the previous
foreground and cursor.

F12 disarms automatic takeover for the remainder of the run.

## One supported entrypoint

From WSL, use `./dev`; it selects the prepared Windows Python, translates
configuration and planner-script paths once, and invokes the checked-in live
launcher without a pseudo-terminal:

```bash
./dev launch --preflight-only
./dev launch
./dev journey --planner subprocess \
  --planner-script scripts/live_direction_smoke_planner.py \
  --planner-arg=--bearing --planner-arg=100 \
  --planner-arg=--distance --planner-arg=350 \
  --continuous --execute --native-assisted \
  --acknowledge-continuous-live --exclusive
```

`journey` defaults to `config/live.longform.yaml`; the other commands default to
`config/live.burnin.yaml`. An explicit repository-relative `--config` is
translated by the wrapper. Do not substitute direct Windows-Python invocations,
manually written native request files, ad-hoc input snippets, or PTY launch
attempts. If `./dev` cannot express or complete the run, repair that supported
path before treating a workaround as evidence.

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
- [ ] A supervised launcher interruption emits no further input and does not
      reclaim focus.
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

A live run is only as stable as the GPU driver. Repeated `BAD STUFF` /
`DXGI_ERROR_DRIVER_INTERNAL_ERROR` device resets on this host were **not** fixed
by lowering graphics settings; they stopped after the Intel Iris Xe driver went
from `32.0.101.6737` to `32.0.101.7088`. Treat a device-reset symptom as a driver
question first.

A clean soak proves only the scene it ran in. A quiet paused town does not clear
water- or effects-heavy locations.

## What live evidence does and does not cover

Live proofs here are single supervised runs on one host and save. They
demonstrate that a path can work, not that it generalizes. A completed
`move_in_direction` smoke does not clear every local route; one Storm House exit
does not clear every building. Treat each as one data point and say so when
citing it.
