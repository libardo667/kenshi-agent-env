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

## The two hard parts

**1. Releasing the old body.** `PlayerInterface::recruit(Character*, bool editor)`
*adds* to the player faction. True shifting also has to let go, or the agent
accumulates a squad of corpses and hosts instead of moving between them. There
is no obvious release API; `getDeadSquad()` suggests Kenshi already relocates the
dead somewhere. **This is the piece to prototype first, because if release is
not achievable the whole mechanic changes shape** — it becomes possession-by-
accretion, which is a different (and worse) game.

**2. Faction consequence.** Recruiting pulls a character out of their faction.
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

One live run, from an authored start, in which the party is deliberately lost
and the agent continues playing in a new body, with the run bundle showing:
the terminal condition detected, the candidate list offered, one
`shift_into_body` bound and succeeded against world outcome, and a subsequent
ordinary operation issued and carried out by the new body.

Steps completed after the shift is the only number that proves it.
