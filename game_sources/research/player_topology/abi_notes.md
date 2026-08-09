# ABI notes

All RVAs are relative to the 64-bit Kenshi image. Signatures come from
KenshiLib 0.4.0 and successful linkage, not recovered public symbols, so they
remain medium confidence.

`ActivePlatoon` is a `RootObjectContainer`, while its `me` ownership member
points to the underlying `Platoon` that owns `getPlatoonStringID()`. The exporter
checks both pointers and the platoon object's validity before using that engine
string. The exported ID adds a `platoon-` namespace prefix. Both
`platoon-Nameless_0` and `platoon-Nameless_1` survived the recorded process
restart and later F9 GameWorld reset with their memberships intact.

Player character handles are only candidate identity. The first authored
management drag changed Paste's handle index and serial as well as her platoon,
contradicting the earlier container-only stability assumption. Current source
therefore preserves the first candidate ID for one live player `Character*` and
`validKey` pair. The registry is player-only and is cleared by
`ResetSessionState` before a new GameWorld can reuse a pointer. The compiled
fixture covers same-object change, pointer reuse, and explicit reset; the live
bundle moves Paste between platoons and back without changing her full ID.

Entity IDs remain session-scoped. F9 incremented `identity_session_id` and the
character-ID session component changed; no cross-session character-ID
continuity is claimed. Platoon string IDs, names, and member names persisted.

Roster membership is reconstructed by asking each current player character for
its `ActivePlatoon`; the active tab is read separately from
`getCurrentActivePlatoon`. Primary and selected set are also separate members.
No pointer or iterator survives the snapshot. A returned getter call proves
only the read completed; later snapshots and an exact save/reload run are
required for behavioral and persistence proof. The named live bundle supplies
those later observations. It also shows that Kenshi restored primary and
selection on F9 but reset the active tab to `Nameless_0`; the exporter reports
those as separate facts instead of inferring one from another.
