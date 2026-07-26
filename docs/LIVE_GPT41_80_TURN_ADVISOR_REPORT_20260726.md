# GPT-4.1 80-turn live run: camera recovery and advisor signal

## Verdict

Run `20260725T80turn-camera-recovery-live-02` completed all 80 authorized
runtime steps against OpenRouter route `openai/gpt-4.1`. Kenshi stayed
responsive and the run finished on its action budget rather than a crash,
safety preemption, stale observation, or planner terminal:

```text
steps_completed=80
terminated=false
success=null
stop_reason=Maximum action count reached after plan completion.
```

This is green endurance-length and controller-integration evidence. It is not a
green task result: the deliberately open objective asked the model to choose its
own goals, and no explicit completion predicate existed.

The most important result is the first live success-path proof for
`recover_camera_view`. The planner requested recovery as one no-argument
semantic action. The controller independently established selected-character
follow, searched its fixed candidate set, scored retained frames, and returned
`recovered` three times. No model-authored camera direction or success
predicate was used.

The run also isolates the next major limitation. Local execution is now
materially more reliable than strategic selection: 13 of 20 dialogue
approaches targeted the Mercenary Captain, repeatedly entering and leaving
bodyguard-hiring branches after the planner had already recorded that Hep could
not afford them. A bounded, read-only, source-grounded strategic advisor is a
better response than adding more controller macros or prompt prose.

## Setup and terminal evidence

- Run ID: `20260725T80turn-camera-recovery-live-02`
- Control mode: `native_assisted`
- Planning mode: `continuous`
- Planner route: OpenRouter `openai/gpt-4.1`
- Protocol: `0.6.1`
- Start: `2026-07-26T01:53:56.802297Z`
- Finish: `2026-07-26T02:12:15.425823Z`
- Wall duration: 1,098.624 seconds (18m18.624s)
- Initial revision: telemetry 25,882 / frame 1
- Terminal revision: telemetry 28,079 / frame 97
- Event log: 67 MiB, 97 retained action/candidate frames
- Event-log SHA-256:
  `05ecf3cfe16f9978e7eaa81349b50ddda63260679083ec43941a1bd027b73992`
- Initial-frame SHA-256:
  `c5254e343cab9e0ee926478010a38c4c14fd70a99349ed9365bfb2ad294f94a5`
- Terminal-frame SHA-256:
  `a00c35fe51391894531fe7e542ee4414498e3434d0906defd931adb40dd2b709`

The initial frame was the obstructed ruined Storm House view from the bounded
failure probe. The terminal frame was a readable view inside The Hub bar with
Hep visible among patrons and traders.

The aggregate summary recorded:

| Measure | Result |
|---|---:|
| Strategic planner calls | 105 |
| Mean / p50 / p95 planner latency | 7.692s / 7.922s / 13.625s |
| Plans proposed / accepted | 73 / 73 |
| Plans completed / aborted | 58 / 15 |
| Plan steps succeeded / failed / cancelled | 68 / 12 / 3 |
| Action receipts / executed actions | 79 / 77 |
| Primitive actions | 284 |
| Observations / stale observations | 5,595 / 0 |
| Input-boundary revalidations / rejections | 78 / 0 |
| Memory writes | 67 |
| Options started / succeeded / failed | 30 / 21 / 9 |
| Option success percentage | 70% |
| Subscriber drops / pump errors | 0 / 0 |
| Safety supervisor preemptions | 0 |

For comparison, the preceding 80-turn run completed only 3 of 30 monitored
options (10%), succeeded on 50 steps, failed on 30, and executed 58 receipts.
This run completed 21 of 30 options (70%), succeeded on 68 steps, failed on 12,
and executed 77 receipts. That is a substantial live execution improvement,
although the different world trajectory prevents treating it as a controlled
benchmark.

## Exact action distribution

| Semantic action | Receipts |
|---|---:|
| `activate_visible_control` | 21 |
| `approach_dialogue_target` | 20 |
| `use_game_binding` | 17 |
| `move_in_direction` | 7 |
| `dismiss_screen` | 6 |
| `recover_camera_view` | 3 |
| `move_to_character` | 2 |
| `purchase_item` | 2 |
| `noop` | 1 |

The outcome classifier marked 73 actions `changed`, four `no_op`, and two
`not_executed`. Both non-executed native actions failed closed: one
`stale_revision` and one `command_already_active`.

The 17 binding actions were five stats toggles, four inventory toggles, four
map toggles, and four pause toggles. The controller never exposed raw camera
keys to the planner.

## Camera recovery: live success path

The three controller-owned transactions were:

| Step | Verdict | Chosen candidate | Best score | Primitives |
|---:|---|---|---:|---:|
| 20 | `recovered` | `angle_orbit_right` | 0.731768 | 8 |
| 22 | `recovered` | `portrait_follow` | 0.723715 | 2 |
| 24 | `recovered` | `angle_orbit_right` | 0.784162 | 8 |

Every winning candidate:

- exceeded the configured 0.72 clear threshold;
- retained Hep's selected-character world label;
- had camera anchor distance 0;
- remained on floor 0;
- carried a fresh screenshot hash and advancing telemetry/frame revisions.

The first transaction advanced from an obstructed score of 0.442 to 0.732.
The third reached 0.784. The second stopped after portrait follow because that
candidate itself passed the threshold; it did not spend the rest of the
transaction budget.

Three adjacent planner requests are still strategically redundant, but they are
not camera finagling. The controller owned 18 bounded primitives and returned
typed evidence each time. The previous run spent its first 30 executed receipts
on model-directed camera/pause manipulation without this semantic boundary.

## Gameplay narrative

Hep began paused inside ruined Storm House with 179 cats, an Iron Club, Rag
Loincloth, and Basic First Aid Kit. The agent:

1. Opened and closed inventory, stats, and map screens.
2. Used several bounded direction moves to leave the obstructed starting area.
3. Recovered the camera and obtained a readable selected-character-follow view.
4. Entered The Hub bar and conversed with Mercenary Captain, Pacifier, Metaru,
   and Barman.
5. Opened Barman trade and bought Greenfruit twice.
6. Continued surveying dialogue branches until the 80-step budget ended.

Initial and terminal selected-character positions were approximately:

```text
start: (-51380.490, 1610.128, 2344.560)
end:   (-51142.190, 1579.069, 2650.806)
```

The net x/z displacement was about 388.04 world units. Authoritative game time
advanced from 8,903.397 to 9,259.454 elapsed minutes, about 356.06 in-game
minutes. Hep remained alive, conscious, out of combat, stationary at the
terminal read, and unchanged at 75.75 blood.

Both Greenfruit transactions were later confirmed in authoritative state:
money fell from 179 to 135 and inventory gained two separate Greenfruit items
at 22 cats each. This is live effect evidence, not merely two successful input
receipts.

## Strategic repetition and the advisor signal

Dialogue approaches by target were:

| Target | Approaches |
|---|---:|
| Mercenary Captain | 13 |
| Barman | 4 |
| Pacifier | 2 |
| Metaru | 1 |

Repeated visible-control choices included:

- `3. Nothing`: six
- `3. Nevermind`: three
- `1. Nevermind`: three
- `1. I'm looking to hire some bodyguards`: three
- Barman trade-opening variants: four total

The planner repeatedly wrote objectives saying it could not afford mercenaries,
should end the conversation, or should seek another opportunity. It then
re-approached the same captain and reopened the same branch. Persistent memory
was present; what was missing was a stronger strategy model capable of turning
Kenshi knowledge and accumulated failure evidence into a materially different
next goal.

A suitable advisor boundary is therefore:

- read-only and unable to dispatch game/controller actions;
- explicitly requested by the playing planner, with an optional deterministic
  stall/cadence offer;
- grounded in a small curated guide corpus with source identity and excerpt
  provenance;
- given a compact current-world and recent-outcome digest;
- returning ranked goals, rationale, constraints, missing information, and
  cautions rather than a `PlanEnvelope`;
- subject to cooldown, per-run call/token budgets, and unchanged-state
  suppression;
- written to typed run events and exposed to later planner calls as advisory
  context, never silently merged into world truth.

The first deterministic stall signal should be repeated interaction with the
same target/branch after an explicit `no_op`, failed option, or memory saying
the branch is unaffordable or exhausted. A fixed low-frequency cadence can
make advice discoverable without forcing a call, but automatic dispatch should
remain separately configurable.

## Recorded defects and evidence caveats

The run did not have a perfectly clean logical trace:

- Step 12 emitted one `OptionLifecycleError`: a failed native movement option
  had no legal successful transition from its terminal `failed` state.
- Two hosted responses were schema-invalid and recovered on later calls: one
  plan had an unreachable step and one `equals` condition omitted `expected`.
  The aggregate `planner_errors=0` field does not count these recovered
  validation attempts, so the raw events are authoritative.
- One Pydantic serializer warning said a condition path contained string
  `game.pause` where an enum was expected. Execution continued.
- The world-state store safely rejected 29 regressing observations. No
  revision conflict, stale planner observation, input-boundary rejection,
  subscriber drop, or pump error followed. The raw `sequence_stall_incidents`
  counter was 3,621 because the 10 Hz observation pump repeatedly samples a
  roughly 2 Hz telemetry producer; it is not 3,621 supervisor stalls.
- Normal action-budget termination still left the world running. No native
  command remained active, but `LiveEnvironment.close()` still lacks a normal
  causally verified final pause.

The existing production safety helper was therefore invoked outside the run.
It made one pause attempt and confirmed `paused=true` at telemetry sequence
28,208. A later audit at sequence 28,228 remained fresh, loaded, paused, with
Hep stationary and no active command. The post-cleanup frame SHA-256 is
`21efa2945cd24c8bf2649e66c20012ce09c0b027f4f7420327b46864e4305a67`.
That cleanup is safety evidence, not run success.

Kenshi remained responsive as PID 27396 with about 4,181 MiB private memory.
The relevant `kenshi.log` tail contained no `BAD STUFF`, DXGI device-removal,
driver-internal, plug-in, or crash marker. The only matched OGRE asset warnings
were timestamped 15:18 at process startup, several hours before this run.

## Retained artifacts

- Typed lifecycle log:
  `runs/20260725T80turn-camera-recovery-live-02/events.jsonl`
- Frames:
  `runs/20260725T80turn-camera-recovery-live-02/frames/`
- Initial frame:
  `runs/20260725T80turn-camera-recovery-live-02/frames/live_frame_000001.png`
- Terminal frame:
  `runs/20260725T80turn-camera-recovery-live-02/frames/live_frame_000097.png`
- Post-cleanup frame:
  `runs/dev-shots/20260726T021333.105480Z-80turn-camera-recovery-live-02-after-safe-pause/live_frame_000001.png`

The event log and frames are the authoritative run record. A later advisor
implementation or replay must not rewrite this historical evidence.
