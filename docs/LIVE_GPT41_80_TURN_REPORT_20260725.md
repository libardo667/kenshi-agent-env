# GPT-4.1 80-turn live endurance report

- Date: 2026-07-25
- Run: `20260725T80turn-gpt41-live-01`
- Code baseline: `f00a8eb` (`main`)
- Protocol: `0.6.1`
- Character/save: Hep in The Hub

## Verdict

The requested endurance length completed: the authoritative terminal event
records `steps_completed=80`. The agent operated live Kenshi for 15 minutes
46.369 seconds without a human intervention, a safety preemption, stale
telemetry, an input-boundary rejection, a renderer failure, or a process crash.

This was not a green task run. Its terminal result is deliberately recorded as
`success=null`, `terminated=false`. The final step failed because a monitored
native movement option exceeded its timeout. Kenshi was also still running and
an accepted native movement command remained active when normal runtime cleanup
returned. An out-of-run safety helper was invoked immediately afterward; a
later telemetry revision confirmed the game paused and that exact command
terminally cancelled with `reason=world_paused`.

In short:

- the 80-turn endurance requirement passed;
- the safety and telemetry infrastructure remained stable during the run;
- the model achieved real camera, dialogue, trade-navigation, and movement
  effects;
- autonomous play quality was mixed;
- camera recovery and native-option termination are now demonstrated product
  defects, not hypothetical concerns.

## Exact run setup

The run explicitly requested `openai/gpt-4.1` through the OpenRouter adapter
with no reasoning-effort parameter. The selected profile was
`config/live.longform.yaml`: continuous planning, native-assisted control,
live screenshots, the generic semantic action catalog, a live world, and all
ordinary input and safety gates.

The command was:

```bash
KENSHI_AGENT_PLANNER=openrouter \
KENSHI_AGENT_OPENROUTER_MODEL=openai/gpt-4.1 \
KENSHI_AGENT_REASONING_EFFORT=none \
./dev journey \
  --config config/live.longform.yaml \
  --objective 'Run an autonomous 80-turn endurance session in Kenshi. Continue through the full action budget and do not choose stop early. Choose evolving, useful town-local goals for Hep and pursue them through observation and replanning. Start by recovering a readable camera view if needed. Use only current observed facts and the declared semantic actions. Keep Hep safe, avoid combat, theft, and crime, avoid repeated purchases without evidence, and recover from failed actions by observing and choosing another useful path. Treat this as open-ended play, not a fixed demonstration.' \
  --planner openrouter \
  --steps 80 \
  --run-id 20260725T80turn-gpt41-live-01 \
  --continuous \
  --execute \
  --native-assisted \
  --acknowledge-continuous-live \
  --exclusive
```

No safety threshold or execution fence was weakened for this run. The live,
native-assisted, continuous, and exclusive-input acknowledgements were
explicit. F12, input-lease revalidation, human-input detection, calibration,
stale-telemetry checks, the independent supervisor, semantic binding, and
at-most-once command identity remained enabled.

The run log does not persist the requested provider/model identifier. The
model route is therefore evidenced by the exact invocation and resolved
configuration, not independently restated in `events.jsonl`. Persisting the
resolved planner route in `run_started` is an open reproducibility improvement.

## Timing and terminal state

| Field | Evidence |
| --- | --- |
| Run start event | `2026-07-25T22:52:59.721544Z` |
| Runtime `started_at` | `2026-07-25T22:52:59.027395Z` |
| Runtime `finished_at` | `2026-07-25T23:08:45.396223Z` |
| Last lifecycle event | `2026-07-25T23:08:45.399211Z` |
| Runtime duration | 946.369 seconds (15m 46.369s) |
| Requested action budget | 80 |
| Completed steps | 80 |
| Terminal success | `null` |
| Environment terminated | `false` |
| Stop reason | Native movement option exceeded its step timeout |

Here, a turn means one committed runtime action-budget step. It does not mean
one provider call. The terminal record, 80 budget reservations, and
`steps_completed=80` are the authoritative endurance evidence. Planner calls
are reported separately below.

## Quantitative results

### Planning and execution

| Metric | Count |
| --- | ---: |
| Successful primary `PlanEnvelope` calls | 80 |
| Primary schema-validation failures | 4 |
| Primary model attempts | 84 |
| Concurrent option-planner attempts/cancellations | 30 |
| Total strategic planner lifecycle records | 114 |
| Plans proposed | 80 |
| Plans accepted / rejected | 77 / 3 |
| Plans completed / aborted | 44 / 33 |
| Plan steps started | 80 |
| Plan steps succeeded / failed | 50 / 30 |
| Additional step-cancellation lifecycle events | 3 |
| Action receipts | 59 |
| Executed / not executed | 58 / 1 |
| Primitive input actions | 95 |
| Persistent memory writes | 67 |

The built-in summary reports 6.189 seconds mean, 6.742 seconds p50, and
10.359 seconds p95 across its planner-latency population. Restricting the
calculation to the 80 successful primary `PlanEnvelope` calls gives a
7.584-second mean, 7.297-second median, 4.766-second minimum, and 14.890-second
maximum. The populations should be named separately in future reports because
the aggregate includes concurrent/cancelled planning lifecycles.

The four primary validation failures occurred at runtime step indexes 0, 0,
20, and 71. They used malformed capability/field conditions or created an
unreachable step. Three otherwise typed plans were rejected at indexes 5, 13,
and 20 because their camera actions supplied no causal success condition.

### Observation, options, native control, and safety

| Metric | Count |
| --- | ---: |
| Observations published | 5,046 |
| Stale observations | 0 |
| Input-boundary revalidations / rejections | 59 / 0 |
| Options prepared / started | 30 / 30 |
| Options succeeded / failed | 3 / 27 |
| Option progress updates | 910 |
| Native acknowledgement lifecycle records | 28 |
| Native accepted / completed transitions | 6 / 2 |
| Native rejected / cancelled transitions | 22 / 3 |
| Supervisor preemptions | 0 |
| Subscriber drops / pump errors | 0 / 0 |
| Revision regressions / conflicts | 9 / 0 |
| Command mismatches | 0 |
| Sequence-stall incidents | 3,306 |

The 3,306 sequence-stall incidents did not represent a telemetry outage. The
world-state pump sampled at 10 Hz while native telemetry normally publishes at
about 2 Hz, so duplicate sequence reads are routine; none became a supervisor
preemption and no observation was stale. This counter currently overstates
operational trouble and should distinguish expected duplicate polling from an
age-qualified stall.

The summary's native accepted/completed/rejected/cancelled values are lifecycle
transition counts, not mutually exclusive unique-command totals. An accepted
command may later be completed or cancelled.

## What the agent actually did

### 1. Camera recovery dominated the opening

The first frame was almost entirely the exterior/interior surface of a Storm
House wall. Hep's label was visible, but the scene was not usable for play.

The first 30 executed action receipts, zero-based 0 through 29, were all camera
or pause bindings. That opening sequence contained:

- `focus_char`: 4 times;
- camera pan forward/back/left/right: 13 times total;
- camera rotate left/right: 12 times total;
- `pause`: once, changing the initially paused game to running.

Full-run `use_game_binding` totals were:

| Binding | Count |
| --- | ---: |
| `camera_rotate_right` | 9 |
| `camera_forward` | 8 |
| `focus_char` | 7 |
| `camera_rotate_left` | 6 |
| `camera_right` | 4 |
| `camera_left` | 3 |
| `camera_back` | 2 |
| `toggle_stats` | 2 |
| `pause` | 1 |

The model never selected the available zoom bindings. It also had no declared
floor-up/floor-down binding, despite being inside a multi-level building.

The result was an improvement, not a successful recovery. The final in-run
frame shows Hep and several nearby characters in the ruined bar, but large
pieces of roof/wall geometry still cover most of the view. The post-safety
frame is the same obstructed view with the pause banner. A changed frame is not
equivalent to a readable or stable camera.

### 2. Dialogue and trade navigation were real and useful

After the camera phase, the model shifted on its own to town interaction:

1. It approached the Barman. Hep moved about 303.96 world units and the
   measured Barman distance fell from about 308.55 to 23.20.
2. It activated `Let's do business`.
3. It scrolled the Barman trade window and closed it.
4. It approached again, chose `Show me your goods`, and closed trade.
5. A later approach was rejected at the input boundary because its revision
   was stale; the agent replanned and succeeded on the next attempt.
6. It reopened trade and attempted one Rice Bowl purchase.

The purchase gesture executed, but money stayed at 179 and inventory did not
change during the 20-second causal window. The attempt therefore failed rather
than being falsely credited. Telemetry exposes the item's base value, not the
seller's authoritative asking price, so the evidence cannot determine whether
the purchase was unaffordable or failed for another UI reason. The model did
the important safe thing afterward: it did not repeat the purchase and closed
trade.

Across all 59 receipts, action kinds were:

| Semantic action | Count |
| --- | ---: |
| `use_game_binding` | 42 |
| `move_to_character` | 5 |
| `approach_dialogue_target` | 4 |
| `activate_visible_control` | 3 |
| `dismiss_screen` | 3 |
| `scroll_screen` | 1 |
| `purchase_item` | 1 |

There were 51 visibly/telemetrically changed outcomes, seven no-ops, and one
not-executed outcome.

### 3. One nonterminal native order poisoned later movement

The agent next chose `move_to_character` toward an Escaped Servant. Hep
travelled about 393.93 world units, from approximately
`(-51137.54, 1578.869, 2653.073)` to
`(-51380.49, 1610.128, 2344.560)`, but the monitored option timed out before a
terminal native success arrived.

Native command `cmd-d98144009ea344d09aa44023bf3d12f4` remained active and
accepted. Later `move_in_direction` requests were rejected with
`command_already_active`. The runtime preserved at-most-once identity and did
not silently duplicate the original order, which is good. However, 21 later
option attempts immediately raised:

```text
OptionLifecycleError:
Native movement option has no successful transition in state 'failed'.
```

The model kept returning to movement instead of issuing a stop/cancel recovery
or changing to a non-movement objective. This exposed two separate weaknesses:

- the controller has no deterministic terminalization path when a native
  option times out but its command remains accepted;
- planner feedback did not make the poisoned movement surface sufficiently
  clear to stop repeated attempts.

The model did briefly open and close the stats screen late in the run, showing
that the active command did not block all non-movement UI actions.

## World-state outcome

| Field | Initial | Last in-run |
| --- | ---: | ---: |
| Telemetry sequence | 4,170 | 6,062 |
| Paused | `true` | `false` |
| Money | 179 | 179 |
| Game elapsed minutes | 8,401.096 | 8,877.145 |
| Hep x | -51,354.120 | -51,380.490 |
| Hep y | 1,614.212 | 1,610.128 |
| Hep z | 2,438.221 | 2,344.560 |
| Hep hunger | 2.592052 | 2.485848 |
| Hep blood | 75.75 | 75.75 |
| In combat | `false` | `false` |
| Active native command | none | `cmd-d981...` |

Net initial-to-final x/z displacement was about 97.30 world units, although
the travelled path was much longer because the agent first approached the
Barman and then crossed back toward the Escaped Servant. Game time advanced
476.049 minutes, about 7.934 in-game hours. Hep never entered combat, blood
remained unchanged, money remained unchanged, and no theft/crime action was
attempted.

## End-of-run safety cleanup

Normal runtime termination left the world unpaused with
`cmd-d98144009ea344d09aa44023bf3d12f4` active. This is a known implementation
gap: `LiveEnvironment.close()` performs no causally verified final pause.

Immediately after the complete run lifecycle had been written, the existing
production safety helper `_ensure_interrupted_safe_state` was invoked outside
the run. It made one pause attempt and confirmed `paused=true` at telemetry
sequence 6,158. At sequence 6,168 the exact native command became terminal:

```text
status=cancelled
reason=world_paused
```

A later completion-audit read remained fresh and advancing, with the game
loaded and paused, movement speed zero, money 179, and no active command.
Kenshi remained responsive as PID 27396. The final 800-line `kenshi.log` scan
contained no plugin error, `BAD STUFF`, DXGI device removal, driver-internal
error, exception, or crash match.

This cleanup is safety evidence, but it is not included in or credited as run
success. The runtime itself must own the same verified finalization behavior.

## Camera research and controller design

The live behavior closely matches long-running player complaints rather than a
model-specific anomaly. Players describe interiors becoming obstructed, the
camera jumping with terrain/height, and spending substantial time getting back
to a character. The most consistent community technique is:

1. lock/follow a character by double-clicking or right-clicking the portrait,
   or by double-tapping the character's number key;
2. rotate and zoom while retaining follow;
3. avoid WASD panning because it breaks the follow lock;
4. explicitly select the correct building floor, commonly floor 0 for a
   ground-floor interior.

Sources:

- [Steam: Camera issues](https://steamcommunity.com/app/233860/discussions/0/4287991687305846378/)
- [Steam: Controlling Camera View](https://steamcommunity.com/app/233860/discussions/0/2650805212050183902/?ctp=2)
- [Reddit: Help about camera](https://www.reddit.com/r/Kenshi/comments/1g7oyb1/help_about_camera/)
- [Reddit: Better/follow camera](https://www.reddit.com/r/Kenshi/comments/ousom5/is_there_any_way_to_get_a_better_camera_in_this/)
- [Reddit: Building interior/floor issue](https://www.reddit.com/r/Kenshi/comments/10ubyuq/building_bugs_idk_what_to_do/)

The installed `controls.cfg` corroborates the usable primitives:

```text
camera pan: W/S/A/D
camera rotate: Q/E
camera tilt: comma/period
camera zoom: Home/End
floor down/up: PgDn/PgUp
focus character: F
free camera: semicolon
```

The current agent exposes pan, rotate, zoom, and `focus_char`, but not floor
up/down. It also presses `F`, which recenters the selected character but is not
proven to establish the persistent portrait-style follow lock. Its recovery
strategy then relies heavily on WASD panning—the exact operation players report
breaks follow.

### Proposed semantic action: `recover_camera_view`

Camera recovery should become a controller-owned best-effort transaction. The
model should author only:

```json
{"kind": "recover_camera_view"}
```

It should not choose directions, pulse counts, coordinates, floor guesses, or
success predicates. A bounded controller implementation should:

1. require a loaded world and one selected character;
2. never unpause a world that began paused; if safety requires pausing a
   running world, report that state explicitly rather than silently resuming;
3. read/export the current building floor and step to the selected character's
   floor, or use bounded floor-down attempts until floor 0 when that is the
   only authoritative target;
4. establish character follow through a tested portrait/number-key gesture,
   not merely assume `F` locks follow;
5. apply a fixed zoom-out baseline;
6. evaluate a small fixed sequence of follow-preserving orbit and tilt
   candidates;
7. capture and score every candidate for selected-character visibility,
   large low-detail/occluding regions, useful scene coverage, and HUD/modal
   interference;
8. select the highest-scoring candidate above a minimum readability threshold;
9. return one typed outcome:
   `already_clear`, `recovered`, or `failed_after_bounded_attempts`, including
   before/after frame evidence and the chosen candidate.

Recovery and intentional camera survey should remain separate concepts. Raw
pan/orbit bindings may still be useful for a deliberate look around, but the
generic planner should not be asked to compose them when it merely needs a
stable readable view.

This design is deterministic in search order, bounded in input, best-effort
against Kenshi geometry, causally checked against resulting frames, and cheap
for the model: it says “recover” once.

## Defects and follow-up order

### Highest priority

1. Add controller-owned `recover_camera_view` with floor control, persistent
   character follow, fixed zoom/orbit candidates, and frame-scored outcomes.
2. Make every normal live-runtime exit own a causally verified safe pause and
   terminalize or explicitly abandon an active native command.
3. Give failed native options a valid cleanup/terminal transition. A timed-out
   option must not leave the entire movement surface poisoned by one accepted
   command.

### Reporting and contract correctness

4. Persist the resolved planner adapter/model and all material runtime
   overrides in `run_started`.
5. Fix the summarizer discrepancy: the event log contains four top-level
   `planner_error` events, while the built-in summary reports
   `planner_errors=0`.
6. Either produce the documented `runs/<run-id>/transcript.log` or stop
   claiming every run has one. This run retained only `events.jsonl` and
   frames.
7. Separate expected duplicate telemetry polls from true age-qualified
   sequence stalls.
8. Eliminate the two Pydantic serializer warnings emitted for the invalid
   string value `camera.position` in an enum field around late camera plans.
9. Export an authoritative asking price before purchase; base item value is
   insufficient for an affordability decision.

## Retained evidence

The primary log is:

```text
runs/20260725T80turn-gpt41-live-01/events.jsonl
lines: 16,167
bytes: 42,073,198
sha256: d7f6baad947c55fe10f2d1044da42b2f3bebc41dd4d73a21dcf8fa57b17446ab
```

The run retained 81 1920x1080 PNG frames:

```text
initial:
runs/20260725T80turn-gpt41-live-01/frames/live_frame_000001.png
sha256: 7775ebbeec45a5c4066d163d6327d2f79adba94c27988609484aa36e0d93792a

last in-run:
runs/20260725T80turn-gpt41-live-01/frames/live_frame_000081.png
sha256: c43fee1369788dc75fd97f1b3fd36c146cd8875811078d88ca40d06b9763f7b2

post-safety pause:
runs/dev-shots/20260725T231117.915217Z-gpt41-80-after-safe-pause/live_frame_000001.png
sha256: 4f425855beeadbecfc5bbe4f1eb174aed4cffbe8cbb0918e98a94484f674e3fa
```

No `transcript.log` exists in the run directory. All metrics in this report
come from the retained lifecycle log, direct frame inspection, the built-in
summary where explicitly identified, and post-run live telemetry/log/process
checks. Mock evidence is not used to upgrade any live claim.
