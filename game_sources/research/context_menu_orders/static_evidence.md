# Static evidence

## Exact inspected inputs

- Installed `kenshi_x64.exe`: Kenshi 1.0.65 x64, Steam build `13871665`,
  SHA-256 `a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1`.
  The PE image base is `0x140000000`; GNU `objdump` confirmed executable bytes
  at every RVA recorded in `call_sites.json`. That confirms address presence,
  not the inferred C++ signature.
- KenshiLib dependency checkout: version 0.4.0 at commit
  `b566d74bf3d74629cc2fb632a97595b8202993f1`. The locally installed
  `KenshiLib.lib` is modified relative to that checkout and is therefore pinned
  separately by SHA-256
  `d407bf18c807cd3390643227ca4dc3ee4628fedc520870eee250201c04db311d`.
  The full include tree hashes to
  `fbfa33a283ed840e70f6f5f6675c2544df89bc18f8901b81eaa7095b466ec4c8`.

## Declarations and executable addresses

KenshiLib 0.4.0 declares `ContextMenu::showContextMenu(bool, RootObject*)` at
RVA `0x7A5960`, followed by the public `ContextMenu::orders` member. It declares
`ContextMenuGUI::show(const lektor<int>&, const std::string&, bool)` at RVA
`0x7A6D80`. This is direct declaration evidence that menu construction and GUI
drawing have separate entry points and that the constructed menu owns an order
list. It does not alone prove that suppressing `ContextMenuGUI::show` is free of
every side effect.

KenshiLib also declares the three rejected candidate predicates:

- `PlayerInterface::isOrderValidForSelection(TaskType) const`, RVA `0x7F1150`;
- `PlayerInterface::getPlayerTaskProbability(TaskType, RootObject*, float&)`,
  RVA `0x7F5380`;
- `Character::checkPlayerOrderForProblems(TaskType, RootObject*)`, RVA
  `0x5D11C0`.

The eventual dispatch is declared as
`PlayerInterface::newPlayerTaskSelectedCharacters(...)` at RVA `0x7F93F0`.
Current native source hooks both menu entry points, reads `orders`, re-probes at
dispatch, and calls that task entry point only after the exact task remains
advertised. None of the three rejected candidate predicates remains a project
call site. Exact repository locations are machine-checked from `call_sites.json`.

## Source inference

The strongest static inference is that calling `showContextMenu(true, target)`
while suppressing its `ContextMenuGUI::show` call yields the same computed list
the normal menu path would draw. Confidence is medium rather than high because
the executable has no public symbols, the signature comes from KenshiLib, and
no retained A/B run bundle compares a real right-click with the silent probe.
