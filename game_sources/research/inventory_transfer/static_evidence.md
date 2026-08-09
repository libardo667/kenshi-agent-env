# Static evidence

The exact Kenshi 1.0.65 x64 executable, Steam build, KenshiLib 0.4.0 checkout,
library artifact, and header tree are fingerprinted in `call_sites.json`.
KenshiLib declares `ForgottenGUI::showTradeWindow` at RVA `0x7905D0` and the
`inventoryWindowsOpen` map keyed by owner `hand`. Current source requests a
pair, waits for two exact open windows in later telemetry, and never treats the
call return as proof that either window appeared.

For movement, KenshiLib declares `InventorySection::getItemAt` at RVA
`0x745AF0`, `Inventory::removeItemDontDestroy_returnsItem` at RVA `0x749800`,
and `Inventory::tryAddItem` at RVA `0x74AB10`. Current source measures source
and destination state, attempts rollback, and reports partial movement when
later destination counts contradict a false `tryAddItem` result.

Shop classification uses `InventoryGUI::getNPCTrader` at RVA `0x70D060`.
Project-owned simplified pricing uses the item's virtual `getValueSingle`, the
actual destination quantity change, and `Inventory::takeMoney` at RVA
`0x744C20`. This path does not reproduce theft, haggling, faction, stolen-goods,
uniform, illegal-goods, or other GUI adjudication.

The retired `InventoryGUI::RClickAutoTrade` path is not an authority. Three
historical live calls crashed with an ABI contradiction: disassembly indicated
pointer parameters where the KenshiLib declaration exposed slot and boolean
values. Rather than guessing through more crashes, current source deletes that
call and states the narrower model-level behavior it owns.
