# Scaffold build report

Generated and checked on 2026-07-22 in a Linux Python 3.13.5 container. This
report distinguishes automated checks from work that requires Windows and a real
Kenshi installation.

## Automated checks completed

- `PYTHONPATH=src pytest -q`
  - Result: **20 passed**.
- `python -m compileall -q src scripts/external_planner_example.py`
  - Result: passed.
- JSON Schema export for action, telemetry, observation, and decision models.
  - Result: all four files generated and parseable.
- Mock-mode doctor command.
  - Result: Python, prompt path, runs path, and mode passed.
- Heuristic mock benchmark, seed 7, 40-step budget.
  - Result: survived one in-game day in 25 decisions.
  - Reflex decisions: 5.
  - Policy rejections: 0.
  - Stale observations: 0.
- Scripted planner smoke test.
  - Result: pause, speed, skill, and stop decisions parsed and executed.
- Subprocess planner smoke test.
  - Result: request/response JSON-lines bridge completed four steps.
- Editable package install was verified locally with build isolation disabled,
  because the execution environment could not fetch build dependencies from its
  package index.

## Checks not completed here

- Ruff and mypy were declared in the development extras but were not installed
  in the execution image; their commands could not be run.
- The native C++ project was not compiled because this environment is not
  Windows and does not have the Visual C++ 2010 x64 toolset or KenshiLib build
  dependencies.
- The DLL was not loaded into Kenshi.
- No live telemetry field was verified against the game UI.
- Windows screenshot capture, focus, SendInput, integrity-level behavior, and
  F12 emergency stop were not exercised.
- The optional OpenAI planner was not called.

## Interpretation

The platform-independent agent runtime is executable and tested. The native
source and live-control path are implementation scaffolds with explicit manual
acceptance gates, not a claim of a finished Kenshi integration.
