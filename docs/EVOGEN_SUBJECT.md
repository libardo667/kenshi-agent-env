# EvoGen subject boundary

G15 registers KAE as the optional `kenshi` entry point in
`evogen.subjects`. The integration package is separate from ordinary
`kenshi_agent` imports and imports EvoGen lazily only after the host loads the
entry point. The optional dependency is pinned to EvoGen public commit
`5e72ca364f0a1b2c5b23d41c9af5a2a15099b946` and is constrained to Python below
3.14 because KAE supports Python 3.14 while EvoGen currently does not.

The KAE-owned factories provide the API 1.1 bootstrap, runner, environment
investigator, builder, adversarial reviewer, evaluator, materializer, doctor,
and conformance fixture. Builder, reviewer, evaluator, and materializer are
distinct authorities. Candidates are written to an isolated scratch
workspace, checked for digest integrity and forbidden literals, evaluated on
the symmetric four-category suite, and materialized only through typed
content-addressed artifacts. The candidate runner recomputes the SHA-256 of
the declared source bytes; improved behavior requires that digest to match the
plugin artifact and the exact generated candidate bytes. Tampered or unrelated
bytes remain blocked. A retained child adds only the synthetic-only
`kae_synthetic_observation` capability, with experiment evidence and
implementation references bound to exact content digests.

The runner is intentionally a synthetic proof fixture. It accepts only the
four opaque G15 scenarios, emits typed events with unique identities and
contiguous ordering, writes traces under the host workspace, and records
explicit synthetic/read-only metadata. An execution receipt never certifies a
world effect; a later independent outcome observation carries that distinction.
The adapter does not instantiate KAE live or replay environments, input
controllers, native transport, application launch, save/DLL paths, or G16 run
bundle ingestion.

The capability manifest is projected from KAE's existing generated authority;
no second hand-maintained capability list is introduced. The doctor verifies
that authority, its typed digest, and CAS linkage before returning a complete
empty diagnostic collection. Missing or corrupt authority raises and is
reported as a diagnostic failure.

## G15 completion boundary

Parent KAE authority: `548658cbcef35037252e63be40248fa6a94b5ec1`.

G15 implementation is complete in scope in this checkout: optional packaging,
metadata discovery, API 1.1 factories, synthetic runner, adversarial candidate
path, doctor, and host conformance proof are implemented and tested. The
candidate is independently reviewed and locally accepted; commit, publication,
and hosted CI remain pending. G16 is next and remains locked pending separate
authorization.

Withheld claims are explicit: this does not prove live Kenshi control, replay
consumption, native/DLL compatibility, save mutation, observer behavior, or
world-effect capability. The adapter's synthetic outcome evidence is not a
live or replay claim.
