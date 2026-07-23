# ADR: Explicit control modes

Status: accepted  
Date: 2026-07-23

## Context

Most Kenshi actions in this project use ordinary Windows input. The existing
`approach_confirmed_vendor` skill is different: its hotkey asks the plugin to
call Kenshi's internal `PLAYER_TALK_TO` player-order method. Calling the whole
plugin read-only or treating both paths as one experiment makes safety claims
and evidence misleading.

## Decision

Every run has one typed control mode:

- `interface_only` is the default. Native command capabilities,
  acknowledgement state, and skills are absent from planner observations.
  Policy and environment boundaries also reject marked native-assisted skills.
- `native_assisted` exposes narrowly reviewed skills marked
  `requires_native_assisted`. Live execution requires the normal configuration
  and CLI gates plus `control.native_assisted_actions_enabled: true` and
  `--acknowledge-native-assisted-control`.

The mode is recorded in observations, action receipts, run lifecycle events,
console/overlay headers, CLI run summaries, and log metrics. Evidence must be
reported separately by mode.

## Consequences

- Existing vendor-approach functionality remains available without being
  mislabeled as interface-only.
- Future continuous plans can bind assumptions and lifecycle evidence to a
  control mode and reject stale cross-mode work.
- Adding another native bridge requires an explicit macro marker, safety review,
  and mode-specific evidence.
- The telemetry sampling path remains observational, but documentation no
  longer calls the entire DLL read-only.

