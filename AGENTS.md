# Project agent instructions

## Supervised live runs

- Every live run, smoke, probe, and soak must use TTS. The supported `./dev run`
  surface enforces narration; do not bypass it with a lower-level command. If TTS
  is unavailable, stop at preflight unless the user explicitly requests silence.

## Keep the model's job simple

- The model chooses gameplay intent and semantic targets. Runtime-owned mechanics
  such as movement time transitions, monitoring, completion, retries, and cleanup
  belong in deterministic code.
- Repeated rejection around a runtime-owned mechanic is an interface defect. Make
  the model surface or compiler simpler instead of adding more prompt instructions.
- Do not start advisory model work for an action expected to finish before that
  advice can affect execution.
