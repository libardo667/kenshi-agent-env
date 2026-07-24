# ADR: Versioned calibration identity as a hard pointer-action gate

Status: accepted for portable/live code; observation of fields beyond client
size is not yet implemented and not yet exercised in live Kenshi

## Context

Live pointer execution required only the exact configured client width and
height, rechecked inside the input lease. That was a real fail-closed brake, but
it is not a calibration *identity*. A fixed normalized click at "76% across,
72% down" also depends on window mode, UI scale, the DPI/client transform, the
keymap, and the exact calibrated macro set. Two hosts at the same client size
can disagree on all of those, and the click would land wrong while passing the
size check.

The prompt's P4 also warns against the opposite error: this gate must not become
the architecture for genuinely semantic UI anchors. A control resolved from live
MyGUI bounds re-read inside the lease is resolution-independent and must not be
forced through a profile match.

## Decision

- Add `CalibrationIdentity`: a strict model with nullable `client_width`,
  `client_height`, `window_mode`, `ui_scale`, `dpi_scale`, `keymap_id`,
  `profile_id`, `profile_version`, and `macro_set_hash`. Every field is
  optional; a missing value stays missing.
- Expose expected identity from configuration
  (`ControlsConfig.expected_calibration_identity()`) separately from the
  observed identity the controller can currently read
  (`InputController.observed_calibration_identity()`).
- Classify every action as one of `coordinate_independent`, `semantic_current`,
  `profile_calibrated`, or `unsupported` (`PointerActionClass`).
  Coordinate-independent and semantic-current actions never require a profile
  match. A skill listed in `controls.semantic_pointer_skills` is treated as
  semantic-current.
- `evaluate_calibration_identity` compares only the fields the expected profile
  actually declares. The result is one of `not_required`, `matched`,
  `mismatched`, or `unknown`:
  - a declared field the host cannot observe is `unknown`, never a match — an
    unread UI scale is not evidence the UI scale is correct;
  - `unknown` takes precedence over `mismatched`, so an incomplete identity is
    never reported as a clean block;
  - only `not_required` and `matched` permit input.
- Attach a `CalibrationReport` (status, action class, reason, expected/observed
  identities, mismatched and unobserved field names) to every pointer-bearing
  receipt, so interface-only and native-assisted evidence both state calibration
  mode.
- Carry calibration into the P3 boundary. Inside the input lease the environment
  computes the report and passes it to `ExecutionToken.revalidate`. A
  profile-calibrated mismatch that appears during the lease wait is rejected by
  the same fence that guards typed conditions, with zero primitives.
- Preserve the proven behavior for the tokenless path (single-step / bare
  `step()`): a calibration mismatch raises, and a client-size mismatch keeps its
  exact `WxH does not match calibrated WxH` message.

## Consequences

Client-size drift with a plan token present is now a graceful boundary
rejection that releases the reservation, rather than a raise the executor must
treat as an ambiguous environment error and conservatively spend. That is
strictly better for an action we know emitted nothing.

The gate fails closed on incomplete information. This has a direct operational
consequence: declaring a calibration field that the controller cannot yet
observe blocks all profile-calibrated input as `unknown`. The Win32 controller
currently observes only client width and height, so only those may be declared
in a live profile today. `window_mode`, `ui_scale`, `dpi_scale`, `keymap_id`,
and the profile/macro hashes are modelled, comparable, and enforced, but
observing them from Windows is a separate slice; until then, declaring them
would (safely) refuse input rather than guess.

This does not claim general resolution support. Two client sizes proven for
semantic startup do not waive calibration for legacy dialogue/trade/map macros,
which remain profile-calibrated.

Automated portable evidence only. No live Kenshi run has exercised the identity
gate beyond the client-size field.
