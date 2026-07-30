# Guide: authored and reproducible scenarios

Use a custom FCS Game Start for declarative setup and a save fixture for dynamic
state. The fixture store lives under `%LOCALAPPDATA%\KenshiAgent\scenarios`;
ordinary saves remain under Kenshi's own save directory.

## Author the source

In `forgotten construction set.exe`, create an isolated mod and add a **Game
Start**. It can reference starting squad templates and set money, town or world
coordinates, relations, research, and race limits. Character templates carry
stats, health, equipment, inventory, and initial slave state.

The reviewed `KenshiAgentScenarios` bytes and their stable catalog IDs are
checked in under `scenarios/`. Install and verify them only while Kenshi and FCS
are closed:

```bash
./dev scenario install-starts
./dev scenario verify-starts
```

Installation refuses to replace different mod bytes, appends one exact enabled
entry without reordering other mods, and backs up `data/mods.cfg` before change.

## Create a source save

Launch one exact catalog entry:

```bash
./dev launch --game-start kae-01-broke-solo
```

The launcher uses fresh semantic controls for **New Game**, traverses the
unordered Game Start carousel by its currently rendered labels until the exact
FCS label is visible, then selects **Begin** and **Confirm**. It pauses, observes
the normal health window, and proves the catalog's exact cats and party size
from fresh telemetry. It never uses Continue for this path.

Stage anything the Game Start cannot express, such as an interior position,
active combat, or exact time. Save under a clear temporary name, pause, and
close Kenshi normally.

## Capture without changing the source

Kenshi must be closed. Declare all matrix axes:

```bash
./dev scenario capture \
  --source-save agent-hub-source \
  --scenario-id hub-outdoor-safe-broke-solo-day \
  --save-id hub-start-v1 \
  --environment outdoor \
  --danger safe \
  --economy broke \
  --party solo \
  --time-of-day day
```

Capture is immutable. A duplicate ID, reused save identity, duplicate bytes, a
missing `quick.save`, or any symbolic link fails closed. Inspect and re-hash all
fixtures with:

```bash
./dev scenario list
```

## Restore and load exactly

Restore is an explicit save mutation, so do it only while Kenshi is closed:

```bash
./dev scenario restore hub-outdoor-safe-broke-solo-day
./dev launch --scenario hub-outdoor-safe-broke-solo-day
```

Restore touches only `KenshiAgentScenario`. It refuses an existing unowned slot.
When replacing a prior managed run, it prints the recovery directory containing
that previous state.

Scenario launch selects **Load Game** and the exact managed save row from fresh
semantic UI bounds. It then pauses, observes the normal health window, proves
all declared axes, and writes a session-bound attestation. A mismatch exits
without claiming scenario evidence.

## Run and aggregate

Use the catalog ID rather than repeating its labels:

```bash
./dev journey \
  --scenario hub-outdoor-safe-broke-solo-day \
  --objective "Survive and establish a repeatable source of income." \
  --continuous --execute --native-assisted \
  --acknowledge-continuous-live --exclusive
```

Journey requires the same fresh native session and matching observable
conditions. Manual `--scenario-*` fields are retained for unverified historical
or diagnostic labels, but their runs cannot increase scenario/save recurrence.

After runs, fold the new evidence into the observed-blocker ledger:

```bash
uv run python scripts/export_blocker_ledger.py
```

Select capability work from fixture-attested recurrence or one genuinely
survival-critical request. Raw rerun count remains reliability evidence only.
