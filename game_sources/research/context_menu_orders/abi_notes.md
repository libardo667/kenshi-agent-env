# ABI notes

The target is a 64-bit PE image with image base `0x140000000`. Every address in
`call_sites.json` is a relative virtual address from the KenshiLib 0.4.0
headers, not an ASLR-adjusted process address. `KenshiLib::GetRealAddress`
resolves the member-function linker stub to the loaded address before RE_Kenshi
installs each hook.

The signatures are inferred from KenshiLib declarations and successful current
linkage. The executable is stripped, so code bytes at the RVAs do not by
themselves prove parameter types, return types, constness, or ownership. This is
why every signature remains medium confidence.

`kenshi/gui/ContextMenu.h` and `kenshi/PlayerInterface.h` both define
`ContextMenu`. The plugin includes the latter and locally restates only the
`ContextMenuGUI::show` declaration needed for member-pointer name mangling. It
does not construct or dereference a locally defined `ContextMenuGUI`.

The hook must call the saved original `ContextMenu::showContextMenu`, not the
hooked entry point, and must restore the probe flag on every exit. Suppressing
`ContextMenuGUI::show` is safe only during that guarded call. A visible existing
menu blocks probing because rebuilding it could replace player-owned UI state.

`ContextMenu::orders` is a Kenshi `lektor<int>`. Values are copied immediately
into project-owned `AdvertisedTask` records; the plugin retains no pointer or
reference to the container across frames. Dispatch uses `TaskType`, `hand`,
`Building*`, and `Ogre::Vector3` exactly as declared, but the ownership and
lifetime guarantees of those types remain Kenshi-owned and are rechecked from
fresh telemetry before dispatch.
