# Guide: running against real Kenshi

Per-run evidence lives in its commit and `runs/<run-id>/`; this is procedure only.

## Choose one control mode

`./dev run` exposes one explicit authority choice:

| Mode | Authorizes |
| --- | --- |
| `plan-only` | planning and observation without gameplay actions |
| `polite-live` | configured gameplay actions with foreground and cursor restoration |
| `exclusive-live` | configured gameplay actions while the agent retains desktop ownership |

The canonical configuration owns planning mode and the available action surface. The dev command
expands a live choice into the lower-level gates; there is no second acknowledgement set to synchronize.

F12 disarms automatic takeover for the remainder of the run.

## One supported entrypoint

From WSL, use `./dev`; it selects the prepared Windows Python, translates the canonical
configuration path once, and invokes the checked-in live launcher without a pseudo-terminal:

```bash
./dev doctor
./dev launch
./dev run --game-start kae-02-funded-solo \
  --campaign fresh-funded-solo --steps 80 \
  --control exclusive-live
./dev telemetry --watch
./dev snapshot --label funded-solo
./dev recover
./dev recover --dismiss-crash
./dev stop
```

`run` reuses a fresh, loaded, command-idle world or launches and loads one under
a single display lease. A stale, terminal, unloaded, or otherwise ambiguous
existing client fails closed with an exact recovery instruction. Every command
uses the one canonical `config/live.yaml`; it is not selectable from the normal
surface. Narration is default and `--no-tts` disables it. Deterministic planner
scripts are not a second live workflow; action-level checks belong in portable
or native conformance tests, while live acceptance uses the ordinary `run` path.
Never substitute direct Windows-Python, native-file, input-snippet, or PTY workarounds.
The parser-owned reference is [`generated/DEV_CLI.md`](generated/DEV_CLI.md).

The canonical live configuration requires the checked-in 30 fps renderer profile and an active
1920x1080 external display. Actual `launch` and live-control `run` commands
switch to external-only mode, verify the laptop panel is off, and restore
extended mode on every handled exit. Any nonzero or interrupted run invokes
`./dev recover`: it causally pauses a loaded world, dismisses only exact owned
inventories after any active native command terminates, leaves Kenshi open, and
restores a stranded display lease. A power loss may still require `Win+P`, then **Extend**.
Exclusive control opens the visible ownership companion when the canonical configuration enables it.

`doctor`, `run`, and `recover` archive a visible terminal crash before doing anything else,
including the newest dump, logs, telemetry, settings, and frame under `runs/crashes/`.
`recover --dismiss-crash` explicitly closes an unsent report after archival; it uses bounded
ordinary input, aborts on human input, and never force-terminates a lingering process.
Human keyboard or mouse input during startup cancels the pending primitive and
yields control. After three quiet seconds, the same visible five-second
takeover countdown used by a run begins; new input resets it and F12
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
Launch health and runs therefore reject any new Event 141 after their
baseline; crash archives include the matching WER metadata and watchdog-dump
identity when Windows exposes them. A clean soak proves only its scene; a quiet
paused town does not clear water- or effects-heavy locations.

## What live evidence does and does not cover

Live proofs here are single supervised runs on one host and save; they show a path can work, not that it generalizes. A completed `move_in_direction` smoke does not clear every local route; one Storm House exit does not clear every building. Treat each as one data point and say so when citing it.
