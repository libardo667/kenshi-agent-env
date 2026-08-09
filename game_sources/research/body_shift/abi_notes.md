# ABI notes

All RVAs are relative to the 64-bit Kenshi image. Signatures come from
KenshiLib 0.4.0 and successful linkage, not recovered public symbols, so they
remain medium confidence. `Character::setFaction` changes the container portion
of a stable handle; code must retain the old handle and call
`updatePlayerSelection` immediately after the move rather than assuming the old
identity remains selectable.

`recruit` may itself change faction and ownership. The current sequence only
calls it for a cross-faction target, then creates and assigns a dedicated squad.
Pointers to `Character`, `Faction`, and `ActivePlatoon` remain engine-owned.
No pointer is persisted across telemetry frames. A successful return from any
one function is not the completion criterion; later selection and faction
telemetry are required.
