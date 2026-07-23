# Changelog

## Unreleased

- Added protocol `0.3.0` causal native vendor commands with caller-owned UUID
  IDs, exact revision/session/selection/target fences, bounded keyed lifecycle
  acknowledgements, replay metrics, and no implicit retry.
- Added lower-latency GPT-5.6 Luna defaults with configurable reasoning effort.
- Added an OpenRouter vision planner with structured outputs, image input, and
  latency/throughput/price provider routing.
- Added planner latency metrics, a flushed terminal decision stream, and a
  translucent always-on-top Windows decision viewer excluded from model captures.
- Added model-selected bounded movement duration and polite Windows input leases
  with idle waiting, user-interruption detection, and foreground/cursor handback.
- Changed action handoff to Alt+Tab away from Kenshi before restoring the cursor,
  preventing infinite edge-scroll when the saved pointer is on another monitor.

## 0.1.0 — scaffold

- Added a deterministic mock agent environment and one-day survival baseline.
- Added strict telemetry, action, observation, decision, receipt, and memory
  models with generated JSON Schemas.
- Added heuristic, scripted, subprocess, and optional OpenAI vision planners.
- Added SQLite memory, JSONL logging, replay environment, and run metrics.
- Added a guarded Windows SendInput controller and client-area screenshot capture.
- Added a read-only KenshiLib telemetry plugin skeleton with atomic JSON output.
- Added live validation, protocol, experiment, safety, and coding-agent guides.
