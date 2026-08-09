# ABI notes

All RVAs are relative to the 64-bit Kenshi image. Header signatures and member
offsets come from the exact KenshiLib 0.4.0 include tree and successful native
linkage. The executable has no public symbols, so inferred signature confidence
remains medium.

The generated header labels `isFreeSlot`, `tryOperate`, and `getGUIWorkers` at
RVAs `0xF7BB0`, `0xF7FF0`, and `0x307100`. Direct inspection of the pinned
Kenshi executable found the corresponding current bodies around `0xF7BF0`,
`0xF8030`, and `0x3075B0`. The small drift is recorded rather than silently
equating generated-header addresses with the current executable.

The plug-in reads `currentOperators` using its declared Ogre allocator-backed
`std::set` type. Successful compilation proves agreement with the linked
KenshiLib artifact, while a live telemetry probe is required to prove that the
loaded Kenshi process accepts the same layout. Invalid handles, duplicate
stable identities, a negative capacity, an absent output section, an invalid
output item, or a producer wire-bound truncation all lower the corresponding
completeness flag. The planner withholds resource operations when required
state is incomplete.

Operator identities use the same session-scoped stable handle registry as the
player roster. They therefore correlate exactly within one identity session;
no cross-load identity continuity is claimed.

The assigned-work channels are character-owned, not resource-owned. A resolved
task subject may identify a target, but a null subject cannot be filled from
selection, proximity, animation, or a matching task name. Such work remains
observed work rather than accepted-operator evidence.
