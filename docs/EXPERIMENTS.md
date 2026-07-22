# Experiment design notes

Start with repeatable, bounded tasks. "Play Kenshi" is not an evaluable task.

## Suggested progression

1. Remain alive for one in-game hour in a safe town.
2. Pause in response to a visible hostile.
3. Open and close the map ten times.
4. Select a specified squad portrait.
5. Treat one controlled injury.
6. Acquire food before a hunger threshold.
7. Travel from the Hub to Squin.
8. Recruit and maintain a second character.
9. Survive one day without reload.
10. Choose and explain a revisable longer-term purpose.

## Conditions to compare

- screenshot only;
- screenshot plus OCR/UI text;
- screenshot plus partial telemetry;
- screenshot plus telemetry and primitive actions;
- screenshot plus telemetry and reusable skills;
- no memory, episodic memory, and fact/episode/commitment memory.

Keep model, prompt, save, UI resolution, and action budget fixed within each
comparison.

## Minimum metrics

- task success and survival time;
- model decisions and primitive actions;
- invalid and policy-rejected actions;
- time spent paused and unpaused;
- telemetry stale events;
- repeated failed procedures;
- hallucinated state claims;
- human interventions;
- perception, planning, control, and integration failure labels.

## Evaluation warning

A single entertaining run is a case study, not evidence of competence. Preserve
it, but also run fixed seeds or equivalent save snapshots and report the full
distribution, including catastrophic failures.
