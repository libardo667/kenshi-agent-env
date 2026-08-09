# ABI notes

All RVAs target the exact 64-bit executable fingerprint. Member and virtual
signatures come from KenshiLib 0.4.0 plus successful linkage and remain medium
confidence. `inventoryWindowsOpen`, `InventoryGUI*`, `Inventory*`, `Item*`, and
`hand` values are engine-owned; current code resolves them fresh and retains no
pointer across frames.

`tryAddItem` returning false is not an atomic no-op. Destination quantity is
the authority for whether part of a stack moved. A failed add triggers rollback,
then a second count; stranded gain is charged and reported as partial movement.

`Item::getValueSingle` is virtual and concrete item classes may override the
base RVA. Its boolean yields the two values Kenshi exposes, but using those
values for an equal-and-opposite money move is project-owned economics, not a
claim that Kenshi adjudicated the transaction.

The `RClickAutoTrade` inferred ABI remains intentionally unresolved. The
KenshiLib declaration contradicted the inspected shipped prologue and three
live calls crashed. No fallback remains in current source.
