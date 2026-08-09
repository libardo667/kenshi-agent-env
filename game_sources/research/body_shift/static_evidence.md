# Static evidence

The inspected target is Kenshi 1.0.65 x64, Steam build `13871665`, executable
SHA-256 `a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1`.
KenshiLib 0.4.0 is pinned by commit, library hash, and include-tree hash in
`call_sites.json`; its library binaries are locally modified relative to that
commit, so the hashes rather than the clean tag are the exact ABI identity.

KenshiLib declares the sequence used by current native source:

- `PlayerInterface::recruit(Character*, bool)` at RVA `0x6920A0`;
- `PlayerInterface::createSquad()` at RVA `0x7F36B0`;
- `Character::setFaction(Faction*, ActivePlatoon*)` at RVA `0x5CB340`;
- `PlayerInterface::updatePlayerSelection(const hand&, const hand&)` at RVA
  `0x7F5EB0`;
- `PlayerInterface::_selectPlayerCharacter(RootObject*, bool, bool)` at RVA
  `0x7F7D00`.

Current code rejects dead, unconscious, animal, or hostile targets, recruits a
cross-faction target, creates a separate squad, carries selection identity over
`setFaction`, sets the current platoon, and selects the target. Those are source
facts. Whether every call has the inferred signature and whether the world
completed the full transition require compiled and live evidence respectively.
