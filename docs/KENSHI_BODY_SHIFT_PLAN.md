# Body shift: surviving the absorbing state

Status: **spec, not built.** Scope here is the narrowest useful slice.

## The problem this exists to solve

A total party loss is an absorbing state. The save continues — Kenshi has no
game-over — so the run does not crash, error, or terminate. It simply stops
mattering: zero living characters, no legal operation, a planner reasoning
forever about a world it can no longer touch. For an instance meant to run
continuously and unattended, that is the worst failure shape available, because
nothing reports it.

Observed live, run `20260806T151213.413667Z`: bandits downed both characters,
the agent planned `wait` for recovery, and stomach bleeding killed them. Every
subsequent turn was well-formed and pointless.

The fix is not to make the agent survive better. It is to stop equating *the
agent* with *the bodies it currently owns*.

## The nesting

Kenshi's own object model is four nested containers, and the player's foothold
in each is separately settable:

| Layer | Kenshi API | What a shift at this layer means |
|---|---|---|
| Faction | `PlayerInterface::setFaction(Faction*)`, `getFaction()` | Change allegiance wholesale. Rewrites every relation at once. |
| Platoon | `PlayerInterface::setCurrentPlatoon(Platoon*)`, `getCurrentPlatoon()` | Move between groups inside a faction. |
| Squad | `PlayerInterface::createSquad()` → `ActivePlatoon*`, `getDeadSquad()` | The active roster the UI drives. |
| Body | `PlayerInterface::_selectPlayerCharacter(RootObject*, bool, bool)` | Which character is primary. Already used by the plug-in. |

`Character::setFaction(Faction*, ActivePlatoon*)` takes faction and platoon
together, so relocating a body is placing it at a (faction, platoon) coordinate
rather than flipping a single field.

**Only the innermost doll is in scope.** The outer three are recorded here
because the shape of the eventual mechanic should be visible from the start,
and because a body shift that ignores them will silently do one of them by
accident — recruiting across a faction boundary *is* a faction event whether or
not anyone modelled it.

## Scope: one trigger, one operation

### Trigger

Total party loss, defined as: no character in the player faction is both alive
and conscious. Kenshi offers `selectedCharactersUnconcious(bool)` for the
selection; the roster question needs `getAllPlayerCharacters` plus per-character
`alive`/`conscious`, both already exported.

Deliberately *not* triggered by: one character down, a losing fight, low health,
or operator preference. Those are strategy. This is the terminal case only.

### Operation: `shift_into_body`

A new typed operation, not a side effect of selection. It changes **who the
agent is**, which is a different kind of authority from every existing
operation, all of which change *what the agent's squad does*.

- **Recipient scope**: a new `RecipientScope` member. Existing scopes
  (`CURRENT_SELECTION`, `PRIMARY`, `EXPLICIT_RECIPIENTS`) all presuppose a
  living selection, which by construction does not exist here.
- **Binding**: one exact candidate character, bound from current telemetry by
  stable id, refusing on absence or ambiguity like every other target binding.
- **Milestone**: `WORLD_OUTCOME_OBSERVED` — the shift succeeded when the new
  body is alive, conscious, in the player faction, and selected. Not when the
  call returned.
- **Risk**: its own budget line. This is not a pointer action or a native order.
- **Idempotency**: `AT_MOST_ONCE`.

### Candidate eligibility

A character is shiftable if it is alive, conscious, not imprisoned, not
enslaved, not `getting_eaten`, not in combat, is not a unique/named story
character, and belongs to a faction not hostile to the player. Nearest eligible
candidate wins; ties broken by stable id so the choice is deterministic.

Eligibility is computed **native-side** and exported as a candidate list, so the
planner chooses among offers rather than proposing an arbitrary entity — the
same discipline as every other affordance.

## Release is already solved, by Kenshi, on every death

The original worry was that `recruit()` only *adds*, so shifting would accrete
hosts rather than move between them, and that no release API was apparent.

Kenshi performs the release itself, constantly. Observed live: when a player
character dies it leaves the active squad and becomes inspectable exactly like
any other non-player character — the player ends with an empty roster and two
bodies still in the world. `PlayerInterface` holds `deadPlayerSquad` and
`getDeadSquad()`, which returns an `ActivePlatoon*`.

The primitive is `setFaction(Faction*, ActivePlatoon*)`, declared on `RootObject`
(vtable offset `0x0`) and overridden by `Character`. Membership is a
**(faction, platoon) coordinate**, and that single call rewrites it. Death is
that call with the same faction and a different platoon: still player faction,
which is why the corpses stay inspectable; different platoon, which is why they
leave the roster.

So the nesting is not a metaphor for the mechanic. It **is** the mechanic:

- **Seize** — `setFaction(playerFaction, playerActivePlatoon)`
- **Release** — `setFaction(<faction>, <other platoon>)`
- **Faction hop** — the same call with the outer coordinate changed
- **Death** — the engine already doing it

One call, four dolls, distinguished only by which coordinates move. Prototype
order is therefore reversed from the original plan: release is evidenced, so the
first thing to prove is a *round trip* — release a living body to a non-player
platoon and seize it back — since anything the engine only ever does to corpses
may behave differently on the living.

## Proven live: release works, and control is not roster membership

The probe (`shift_body_platoon`, diagnostic-only wire command) released a living
Bombingham out of the active platoon into the dead squad, same faction. It
completed. What the resulting game state showed:

- He left the squad roster while alive — confirmed in Kenshi's own squad screen,
  and he appeared in no squad at all.
- Squad movement orders moved only the remaining roster member.
- **Left-clicking his model selected him and moved him normally.** A released
  body is still fully controllable.

That last point reshapes the design. **Control follows selection, not roster
membership.** The roster is bookkeeping and UI. A shift therefore does not need
roster manipulation to *command* a body — it needs it for the squad UI to make
sense and for selection-scoped orders to stay coherent.

### The invariant the probe found by breaking it

Releasing without deselecting leaves a selection entry with no roster slot, and
three things break at once:

1. `selected_character_ids` (built from `player->selectedCharacters`) and the
   per-character `selected` flags contradict each other, which fails
   `TelemetrySnapshot`'s own validator — telemetry stops being readable at all.
2. Kenshi's squad portraits desynchronize: the portrait list maps index onto the
   selection set, so clicking one character's portrait focuses a different
   character. Recovering required clicking the character's model, not the
   portrait.
3. It is exactly the `selection_size_differs` condition the native command
   validator refuses on, which would make every subsequent order unauthorizable.

**Invariant: `player->selectedCharacters` must remain a subset of the active
roster.** Release deselects; seize reselects. `RootObject::unselect()` and
`PlayerInterface::_selectPlayerCharacter` are the two halves.

This is the fourth instance today of two authorities disagreeing about "the
selection" — after the affordance gate, the boundary observation, and
`selection_mismatch`. The pattern is worth naming as a standing hazard rather
than a series of coincidences.

## The remaining hard part

**Faction consequence.** Recruiting pulls a character out of their faction.
Shifting into a Holy Nation paladin plausibly creates a deserter and makes the
faction hostile to the body now being worn. That is an *outer doll* moving
because an inner one did. It must be observed and reported, not assumed benign.

## Telemetry additions

- `shift_candidates`: eligible characters with id, name, distance, faction, and
  the eligibility reasons that passed.
- `player_faction_id` / `player_platoon_id`: the outer coordinates, so a shift's
  side effects on the outer dolls are visible rather than inferred.

Both are additive; `CharacterState` already carries `alive`, `conscious`,
`imprisoned`, `enslaved`, `getting_eaten`, and `in_combat`.

## What must not happen

- A shift must never be reachable while any player character is alive and
  conscious. Guarded native-side, not only by planner instruction.
- A failed shift must name which eligibility condition refused, per character.
  `selection_mismatch` stood for six conditions and cost a full day of live
  diagnosis; this operation must not repeat that.
- A shift must not be inferable from a defaulted field. Every new observation
  field gets an owner in `WORLD_FACT_FIELDS` / `RUNTIME_CONTEXT_FIELDS`.

## Acceptance

First, a round trip proven in isolation: a living body released to a non-player
platoon and seized back, with telemetry showing it leave and rejoin the roster.
Only then the full case.

One live run, from an authored start, in which the party is deliberately lost
and the agent continues playing in a new body, with the run bundle showing:
the terminal condition detected, the candidate list offered, one
`shift_into_body` bound and succeeded against world outcome, and a subsequent
ordinary operation issued and carried out by the new body.

Steps completed after the shift is the only number that proves it.
