# Body shift: implementation and open-proof record

Status: **implemented and planner-visible; full live proof remains open.**

This document records the mechanic that exists now and the evidence still
missing. It replaces the original total-loss-only specification.

## Current decision

`shift_into_body` is elective. The planner may choose any currently offered
eligible nearby body even while the current squad is alive and conscious.

That policy deliberately superseded the original restriction that body shifting
could occur only after total party loss. A mechanic available only while dying
could not be practised, tested repeatedly, or used as an ordinary part of play.
Total loss remains the motivating recovery case, and the transport still permits
the empty-selection request needed for it, but it is no longer the authorability
gate.

## Implemented contract

The current operation has one exact target and no pointer path:

- The affordance adapter enumerates every reported nearby character that is
  conscious, non-animal, and non-hostile. It does not ask whether the player
  roster is empty.
- Binding re-resolves one exact current stable ID and fails closed if the target
  is absent, ambiguous, stale, dead, unconscious, animal, or hostile.
- The operation registry assigns `NAMED_BODY` recipient scope,
  `WORLD_OUTCOME_OBSERVED` completion, `AT_MOST_ONCE` idempotency, one native
  action of risk, and the `shift_into_body` wire command.
- Native request schema 1.4 permits an empty selected-recipient list for this
  command because the named body is the recipient. Other recipient-bound
  commands still refuse an empty basis.
- The native handler resolves the exact target, applies the reviewed body-shift
  sequence, and waits for its terminal selection check. Exact Kenshi
  declarations, call sites, addresses, signature confidence, and observations
  belong to the canonical
  [body-shift research object](../game_sources/research/body_shift/conclusion.md).
- The terminal acknowledgement reasons are
  `shift_body_recruited`, `shift_body_recruited_forced`, or
  `shift_body_already_held`.

## Source-proven

- `src/kenshi_agent/affordances.py` and
  `src/kenshi_agent/operation_definitions.py` implement elective enumeration,
  exact binding, and the sole interaction contract. There is no total-loss
  authorability condition.
- `src/kenshi_agent/core/transport.py` and
  `src/kenshi_agent/core/telemetry.py` classify body shift as naming its own
  recipient, so its request can carry no selected characters without weakening
  the recipient rule for other commands.
- `native/KenshiAgentTelemetry/KenshiAgentTelemetry.cpp` implements the current
  handler. The research object, rather than this operation record, owns all
  claims that relate that code to Kenshi declarations or binary call sites.
- The diagnostic-only `shift_body_platoon` command remains a separate manual
  probe. It is not a planner operation and is not an old fallback for
  `shift_into_body`.

## Test-proven

- `tests/test_body_shift_after_total_loss.py` constructs an empty-roster,
  empty-selection world and proves an eligible body is offered, binds under
  `NAMED_BODY`, produces a recipient basis containing that body, and validates a
  schema 1.4 request with no selected recipients.
- The same module proves the offer remains available with a living selected
  squad member. This pins the elective policy rather than merely omitting the
  old restriction.
- The `valid_body_shift_request.json` native fixture carries an empty selection.
  Python's strict model and the compiled C++ protocol-fixture target both parse
  that same document and retain its exact target-owned recipient shape.
- Registry and catalog tests prove the operation has one definition, handler,
  adapter, wire owner, and interaction contract.

These are portable structural proofs. They do not prove that Kenshi changed a
live save.

## Live-proven

No named durable run bundle currently proves the complete body-shift operation
under the proof ledger's definition of `live_proven`. The canonical research
object records the historical probe and precisely why its live conclusion is
withheld; this implementation record does not duplicate that argument.

Run `20260806T151213.413667Z` proves the motivating failure shape only: both
characters died, the save continued, and later planner turns had no useful body
to command. It is not proof that a shift recovered that run.

## Withheld and open proof

- Prove one elective shift in a named bundle from pre-dispatch telemetry through
  the exact request and acknowledgement to later roster, faction, platoon,
  primary-selection, camera, and ordinary-action evidence.
- Prove the original recovery case in a named bundle: total party loss, an
  offered body with an empty selection, successful entry, then at least one
  ordinary operation carried out by the new body.
- Measure faction and relationship consequences across more than the neutral
  Drifters case. Hostile bodies remain withheld.
- The current eligibility rule does not inspect imprisonment, enslavement,
  combat, uniqueness, story role, or `getting_eaten`. Those restrictions from
  the original specification were never implemented and must not be described
  as current safeguards.
- Only bodies present in bounded `nearby_entities` can be offered. Distant body
  discovery is not implemented.
- Repeated shifts, save/load after a shift, and long-run squad/platoon cleanup
  remain unproven.

For every future live proof, record the built and installed DLL hashes, exact
run bundle, pre-dispatch state, request, acknowledgement, later engine evidence,
and final disposition. A completed call or acknowledgement alone is never proof
that the save changed.
