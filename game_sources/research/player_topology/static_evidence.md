# Static evidence

The inspected target is Kenshi 1.0.65 x64, Steam build `13871665`, executable
SHA-256 `a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1`.
KenshiLib 0.4.0 is pinned by commit, library hash, and include-tree hash in
`call_sites.json`; its library binaries are locally modified relative to that
commit, so the hashes rather than the clean tag are the exact ABI identity.

KenshiLib declares distinct owners for each topology fact:

- `PlayerInterface::getAllPlayerCharacters()` returns the player roster;
- `Character::getPlatoon()` links one character to an `ActivePlatoon`;
- `ActivePlatoon::me` reaches the underlying `Platoon`, whose
  `getPlatoonStringID()` supplies the engine string ID;
- `ActivePlatoon::getName()` supplies the platoon name;
- `PlayerInterface::getCurrentActivePlatoon()` supplies the current active
  platoon container;
- `PlayerInterface::selectedCharacter` is the primary handle;
- `PlayerInterface::selectedCharacters` is the selected handle set.

ForgottenGUI was inspected separately. It exposes selection-change and selected
player-character UI methods, but does not own the roster-to-platoon topology.
The current exporter therefore reads player, character, and platoon structures
and uses ForgottenGUI only for UI state elsewhere.
