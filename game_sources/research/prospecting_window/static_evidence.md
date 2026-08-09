# Static evidence

The exact binary and KenshiLib identities are in `call_sites.json`. KenshiLib
declares `ZoneManager::getResource` and `getResourceBase`, but both require an
`AreaBiomeGroup*`; no safe current call site can supply that ownership context.
The earlier direct sampler is deleted.

KenshiLib separately declares `ProspectingWindow::getSingleton()` at RVA
`0x337F50`, `showT(...)` at RVA `0x48E260`, the window's last position, skill,
name, and its `ResourceLinePanel` vector. Each resource line exposes its button.
Current source invokes `showT` through the game's window model and copies the
button captions verbatim into one completed survey record. It does not parse
the caption into invented resource/value fields.

The GUI extraction under `game_sources/kenshi/gui_layouts.json` independently
records the shipped `Kenshi_ProspectingWindow.layout`, establishing the window
and its declared widget surface without proving runtime values.
