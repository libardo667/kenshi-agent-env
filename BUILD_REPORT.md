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

- The DLL was not loaded into Kenshi.
- No live telemetry field was verified against the game UI.
- Windows screenshot capture, focus, SendInput, integrity-level behavior, and
  F12 emergency stop were not exercised.
- The optional OpenAI planner was not called.

## Interpretation

The platform-independent agent runtime is executable and tested. The native
source and live-control path are implementation scaffolds with explicit manual
acceptance gates, not a claim of a finished Kenshi integration.

## Windows native follow-up

Completed later on 2026-07-22 from the WSL checkout using Windows-hosted build
tools and local Windows intermediate/output directories:

- Ruff passed, strict mypy passed for 36 source files, and 27 pytest tests
  passed.
- Visual Studio 2022 Build Tools discovered the installed VS2010 SP1 `v100` x64
  compiler (`16.00.40219.01`) and MSBuild platform integration.
- `KenshiLib_Examples_deps` was pinned at
  `b566d74bf3d74629cc2fb632a97595b8202993f1`; Git LFS fsck passed and Boost
  1.60 was extracted.
- `scripts\build_native.ps1` produced a 50,688-byte PE32+ x64 DLL.
- The staged DLL exported `startPlugin`, imported `KenshiLib.dll`, `MSVCP100.dll`,
  and `MSVCR100.dll`, and had SHA-256
  `555cee28d7718bee63e8369ca1462a7a2584e0648e4651f49a992a41e612fc13`.

Runtime loading and live telemetry validation remain intentionally separate.
