# `./dev` command reference

Generated from `kenshi_agent.tooling.dev_cli`; do not edit by hand.
Regenerate with `python scripts/export_dev_cli.py`.

## `./dev`

```text
usage: ./dev [-h] {doctor,launch,run,telemetry,affordances,snapshot,recover,stop,scenario,setup} ...

Safe, state-aware Kenshi live development. Use 'run' for the normal launch-and-agent workflow.

positional arguments:
  {doctor,launch,run,telemetry,affordances,snapshot,recover,stop,scenario,setup}
    doctor                    Check every launch prerequisite without sending input.
    launch                    Launch Kenshi without starting an agent.
    run                       Use a safe loaded game or launch one, then run the agent.
    telemetry                 Print the current player-readable telemetry as JSON.
    affordances               Show the affordance menu the agent would be offered right now.
    snapshot                  Capture one frame with its matching telemetry evidence.
    recover                   Leave Kenshi safely paused and release stranded display ownership.
    stop                      Safely pause and close Kenshi.
    scenario                  Manage reproducible starts and immutable save fixtures.
    setup                     Apply an explicit reversible host repair.

options:
  -h, --help                  show this help message and exit

Examples:
  ./dev doctor
  ./dev run --objective 'Reach Squin' --control live
  ./dev telemetry --watch
  ./dev recover
```

## `./dev doctor`

```text
usage: ./dev doctor [-h] [--timeout TIMEOUT] [--scenario SCENARIO | --game-start GAME_START]

Check Steam, memory, graphics, display, crash, and selected start state.

options:
  -h, --help               show this help message and exit
  --timeout TIMEOUT        Maximum seconds for bounded readiness checks. (default: 60.0)
  --scenario SCENARIO      Use this exact restored and attested scenario fixture. (default: None)
  --game-start GAME_START  Start this exact bundled authored start and prove its initial state.
                           (default: None)
```

## `./dev launch`

```text
usage: ./dev launch [-h] [--timeout TIMEOUT]
                    [--scenario SCENARIO | --game-start GAME_START | --title] [--resume-launcher]
                    [--focus-display]

Launch Kenshi and optionally load one exact start source.

options:
  -h, --help               show this help message and exit
  --timeout TIMEOUT        Maximum seconds for each bounded startup wait. (default: 60.0)
  --scenario SCENARIO      Use this exact restored and attested scenario fixture. (default: None)
  --game-start GAME_START  Start this exact bundled authored start and prove its initial state.
                           (default: None)
  --title                  Stop at the title screen instead of loading a world. (default: True)
  --resume-launcher        Resume one verified pre-game launcher left by an interruption. (default:
                           False)
  --focus-display          Temporarily switch to the external 1920x1080 display only; the default
                           keeps the internal panel and external display active. (default: False)
```

## `./dev run`

```text
usage: ./dev run [-h] [--timeout TIMEOUT] [--scenario SCENARIO | --game-start GAME_START]
                 [--objective OBJECTIVE] [--campaign CAMPAIGN] [--steps STEPS] [--run-id RUN_ID]
                 [--control {plan-only,live}] [--focus-display]

Run the agent in a fresh or already-loaded world. Ambiguous live state fails closed.

options:
  -h, --help                  show this help message and exit
  --timeout TIMEOUT           Maximum seconds for each bounded startup wait. (default: 60.0)
  --scenario SCENARIO         Use this exact restored and attested scenario fixture. (default: None)
  --game-start GAME_START     Start this exact bundled authored start and prove its initial state.
                              (default: None)
  --objective OBJECTIVE       Override the configured objective for this run. (default: None)
  --campaign CAMPAIGN         Save-lineage identity used for durable memory continuity. (default:
                              None)
  --steps STEPS               Override the configured step ceiling. (default: None)
  --run-id RUN_ID             Exact run identifier; generated when omitted. (default: None)
  --control {plan-only,live}  plan-only sends no gameplay actions; live takes desktop input
                              ownership for the run. (default: plan-only)
  --focus-display             Temporarily switch to the external 1920x1080 display only; the default
                              keeps the internal panel and external display active. (default: False)
```

## `./dev telemetry`

```text
usage: ./dev telemetry [-h] [--watch] [--interval INTERVAL]

options:
  -h, --help           show this help message and exit
  --watch              Emit newline-delimited snapshots until interrupted. (default: False)
  --interval INTERVAL  Seconds between watched snapshots. (default: 1.0)
```

## `./dev affordances`

```text
usage: ./dev affordances [-h] [--watch] [--interval INTERVAL] [--json] [--capture CAPTURE]

options:
  -h, --help           show this help message and exit
  --watch              Re-render whenever the menu changes until interrupted. (default: False)
  --interval INTERVAL  Seconds between telemetry reads while watching. (default: 1.0)
  --json               Emit newline-delimited menu payloads instead of a rendered menu. (default:
                       False)
  --capture CAPTURE    Append every distinct menu to this newline-delimited JSON file. (default:
                       None)
```

## `./dev snapshot`

```text
usage: ./dev snapshot [-h] [--label LABEL]

options:
  -h, --help     show this help message and exit
  --label LABEL  Filesystem-safe evidence label. (default: snapshot)
```

## `./dev recover`

```text
usage: ./dev recover [-h] [--timeout TIMEOUT] [--dismiss-crash]

options:
  -h, --help         show this help message and exit
  --timeout TIMEOUT  Maximum seconds for each bounded recovery wait. (default: 15.0)
  --dismiss-crash    After archiving a visible crash, explicitly dismiss its unsent reporter.
                     (default: False)
```

## `./dev stop`

```text
usage: ./dev stop [-h] [--timeout TIMEOUT]

options:
  -h, --help         show this help message and exit
  --timeout TIMEOUT  Maximum seconds for safe pause and close confirmation. (default: 15.0)
```

## `./dev scenario`

```text
usage: ./dev scenario [-h] ACTION ...

positional arguments:
  ACTION
    list            List and verify captured fixtures.
    install-starts  Install and verify the exact bundled authored starts.
    capture         Copy one closed save into the immutable fixture store.
    restore         Restore a fixture into the reserved project-owned save slot.

options:
  -h, --help        show this help message and exit
```

## `./dev scenario list`

```text
usage: ./dev scenario list [-h]

options:
  -h, --help  show this help message and exit
```

## `./dev scenario install-starts`

```text
usage: ./dev scenario install-starts [-h]

options:
  -h, --help  show this help message and exit
```

## `./dev scenario capture`

```text
usage: ./dev scenario capture [-h] --source-save SOURCE_SAVE --scenario-id SCENARIO_ID --save-id
                              SAVE_ID --environment {indoor,outdoor} --danger {hostile,safe}
                              --economy {broke,funded} --party {solo,squad} --time-of-day
                              {day,night}

options:
  -h, --help                  show this help message and exit
  --source-save SOURCE_SAVE   Closed Kenshi save directory to copy. (default: None)
  --scenario-id SCENARIO_ID   New immutable fixture ID. (default: None)
  --save-id SAVE_ID           Stable source-save identity. (default: None)
  --environment {indoor,outdoor}
                              Observable environment axis. (default: None)
  --danger {hostile,safe}     Observable danger axis. (default: None)
  --economy {broke,funded}    Observable economy axis. (default: None)
  --party {solo,squad}        Observable party axis. (default: None)
  --time-of-day {day,night}   Observable time axis. (default: None)
```

## `./dev scenario restore`

```text
usage: ./dev scenario restore [-h] scenario_id

positional arguments:
  scenario_id  Exact fixture ID to restore.

options:
  -h, --help   show this help message and exit
```

## `./dev setup`

```text
usage: ./dev setup [-h] ACTION ...

positional arguments:
  ACTION
    graphics  Install the canonical live configuration's reversible graphics settings.

options:
  -h, --help  show this help message and exit
```

## `./dev setup graphics`

```text
usage: ./dev setup graphics [-h]

options:
  -h, --help  show this help message and exit
```
