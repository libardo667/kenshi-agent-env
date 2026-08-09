# Static evidence

The inspected target is Kenshi 1.0.65 x64, Steam build `13871665`, executable
SHA-256 `a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1`.
KenshiLib 0.4.0 is pinned by commit, library hash, and include-tree hash in
`call_sites.json`. The directly relevant headers additionally hash to:

- `UseableStuff.h`: `8796c1e702dd9807ed9485c89ebcd6a6172aa7b36baca7a2d7151c939d192434`;
- `ProductionBuilding.h`: `11096ce8314a31934edfc06925a2856ca8038bc720df9db4cfbe0132c4fd6f9b`;
- `Inventory.h`: `69a23163f6f58d24b7e5474f71873c5fb0d5b3bfa5e62ef745bc766a5391cc59`.

`UseableStuff` declares `numOperatorsMax` at object offset `0x3AC` and the
`currentOperators` handle set at offset `0x3D0`. It separately declares
`isFreeSlot`, `tryOperate`, `stopOperating`, and `getGUIWorkers`. In the current
executable, the admission routines around RVAs `0xF7BF0` and `0xF8030` compare
the set size at `0x3E0` with capacity at `0x3AC`; the latter inserts the handle
into the set only after that check. The current `getGUIWorkers` implementation
around RVA `0x3075B0` reads the same set size, capacity, and set members. This is
independent corroboration that the fields own accepted membership rather than
selection or animation state.

`Inventory::getSection("out")`, `InventorySection::getItems`, and each valid
`Item`'s name, quantity, and type expose the exact current output stacks. The
producer exports all stacks or marks the inventory incomplete; the bounded
production terminal continues to sum exact positive quantities in that section.

Character ordinary orders, Jobs, permanent Jobs, and current activity are
already exported from their separate AI owners. A task subject may be null and
matching task names do not prove either resource assignment or accepted
operation. No separate resource-owned assigned-worker collection was found.

`hasProgressBarWhenUsed`, `progressBarLevel`, `ProductionBuilding::getOutput`,
`productionState`, and the GUI method `setOutputProgress` exist. Their names and
types do not establish which value applies to a natural resource, whether it is
normalized, or whether it is monotonic across output rollover. No work-progress
field is exported from this evidence.
