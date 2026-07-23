# Kenshi Agent Evolution Prompt — Continuous Planner Upgrade

## Objective

Transform the Kenshi agent from a stepwise reactive controller into a **persistent, intention-driven planner capable of executing multi-step behaviors over time while remaining safe and interruptible**.

The system must preserve all existing safety constraints (pause gating, bounded skills, telemetry validation) while introducing continuity, chaining, and adaptive behavior.

---

## Core Principle

Do not increase raw capability first.

Instead:

> Increase **temporal coherence of behavior**.

The agent should feel like it is *doing something over time*, not repeatedly deciding what to do next.

---

## Phase 1 — Intent Persistence

Introduce a top-level `IntentState`:

```python
class IntentState:
    goal: str
    plan: list[str]
    current_step: int
    confidence: float
    created_at_step: int
    last_progress_step: int
```

### Requirements

* Persist across steps
* Only regenerate when:

  * plan fails
  * no progress for N steps
  * goal completed
* Planner must receive previous intent as input

### Anti-goal

Do NOT recompute full plan every step.

---

## Phase 2 — Action Sequencing

Extend `PlannerDecision`:

```json
{
  "intent": "...",
  "action_sequence": [
    {"skill": "...", "args": {...}},
    {"skill": "...", "args": {...}}
  ],
  "continue_after": true
}
```

### Executor changes

* Execute sequence atomically across multiple pulses
* After each action:

  * check telemetry freshness
  * allow interruption
* Abort sequence if:

  * safety violation
  * stale telemetry
  * unexpected state

---

## Phase 3 — Progress Tracking

Add evaluation after each action:

```python
progress = evaluate_progress(previous_obs, current_obs, intent)
```

Track:

* distance moved
* target proximity
* state change (money, inventory, position)

Update:

```python
intent.last_progress_step = current_step
```

If no progress:

* increment stagnation counter
* trigger replan threshold

---

## Phase 4 — Lightweight Reflection

After each step, generate:

```json
{
  "progress_made": true | false | uncertain,
  "reason": "...",
  "adjustment_needed": true | false
}
```

Use to:

* modify current plan
* NOT fully replan unless necessary

---

## Phase 5 — World Memory Integration

Extend memory into structured state:

```json
{
  "locations": {},
  "entities": {},
  "events": []
}
```

Planner must:

* reference known locations
* reuse discovered knowledge
* avoid rediscovering same things repeatedly

---

## Phase 6 — Time Awareness

Track:

```python
steps_since_progress
total_elapsed_steps
```

Rules:

* if `steps_since_progress > threshold` → replan
* if wandering too long → escalate goal

---

## Phase 7 — Behavior Modes

Introduce modes:

```python
mode = ["explore", "goal_directed", "recover", "idle"]
```

Planner selects mode based on:

* health
* hunger
* progress
* environment

---

## Phase 8 — Safety Preservation

Maintain:

* no direct unpause from model
* bounded pulses only
* telemetry-confirmed re-pause
* no raw clicks outside envelopes

New requirement:

> Action sequences must be decomposable into safe primitives.

---

## Phase 9 — Evaluation Metrics

Add logging for:

* intent duration (steps)
* % actions contributing to goal
* replan frequency
* loops detected
* successful multi-step completions

---

## Phase 10 — Success Criteria

System is considered improved when:

1. Agent maintains a goal across 10+ steps
2. Agent completes a multi-step task (e.g. approach + interact)
3. Replans only when necessary
4. Behavior appears continuous rather than discrete

---

## Guiding Constraint

At all times:

> Prefer a slightly dumber but continuous agent over a smarter but reset-every-step agent.

Continuity is the capability multiplier.

---

## Final Note

Do not attempt to solve dialogue, trading, or combat next.

Solve **temporal coherence first**.

Everything else will build on it.
