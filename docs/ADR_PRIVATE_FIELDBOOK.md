# ADR: Private fieldbook authority

## Decision

Longer campaign work uses a structured private fieldbook in the same
campaign-scoped SQLite database as durable memory.

The fieldbook has runtime-owned project and entry identities. Planner-authored
operations are accepted only through a runtime authority bound to the exact
planner input that supplied every referenced project and evidence ID. Plans
commit after validation, decisions after their action receipt, and patches only
when applied.

Automatic planner context contains a bounded project index and, at most, the
selected active project's summary. Full entries require a bounded elective read
whose identified result reaches exactly the next planner call. Reads and writes
emit no controller primitives and create no world command.

Project prose has no world authority. Telemetry remains authoritative for
inventory, money, location, and current conditions. A Markdown rendering is a
disposable view and is never read back.

## Consequences

- Projects persist across process restarts without creating a second continuity
  database or arbitrary filesystem paths.
- Notes can organize delivery, route, incident, vendor, equipment, and journal
  work without feeding every prior sentence back into every plan.
- Observation, manifest, expense, incident, and route claims retain typed
  evidence provenance; self-authored notes, decisions, and questions remain
  explicitly non-world-authoritative.
- Store failures roll back the transition, produce typed planner feedback, and
  share the durable-store quarantine with memory while gameplay continues.
