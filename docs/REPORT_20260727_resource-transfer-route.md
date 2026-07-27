# Resource transfer route — 2026-07-27

Write-once. Compares two fresh authored-start runs and the supported shutdown
path. Supersede with a later dated report.

## Claim boundary

No autonomous resource collection or income is proven. Earlier Copper in the
selected inventory was manually collected and is excluded. Both runs below
started from `kae-01-broke-solo`, with 20 cats, one character, starting gear,
and zero ore.

## Source-only failure

`fresh-resource-live-20260727-01` retained one exact Iron Resource production
option through `resource_output_ready`, opened that same target's contextual
inventory, and exposed one to four Raw Iron in its output. Four exact
`collect_resource_output` gestures produced source loss zero and destination
gain zero.

The last frame shows the pointer centered on the Raw Iron cell with its tooltip
visible. The failed transfer was therefore not a coordinate miss. Telemetry
reported one open inventory window: the resource source. The operator then
confirmed Kenshi's UI rule directly: right-click does not move ore unless the
player inventory is already open.

This separates two facts the prior contract conflated:

- `open_context_inventory` opens the source;
- `toggle_inventory` opens the selected character destination.

## Contract correction

`collect_resource_output` now requires exactly those two observed inventory
windows, complete controls, one exact selected-character owner, and zero active
shop traders. Kenshi may label this non-commercial two-window layout `trade`;
window ownership and native trader count therefore govern authority, not the
coarse screen label. Source-only dispatch emits zero input.

The success proof remains unchanged: a causally later complete observation must
show equal source loss and selected-character gain. Seeing either quantity alone
does not pass.

The new binder tests were first observed red. Mutating the required window count
from two to one then killed the valid two-window test; restoring it made the
focused suite green.

## Follow-up live run

`fresh-resource-live-20260727-02` reached the same exact Iron Resource from a
fresh start and showed Pao's `Operating machine` goal at distance 38. Two
production options were accepted but cancelled when their 60-second monitored
timeouts triggered safe pauses before output. The planner later proposed
`toggle_inventory` three times without an observable causal success condition.
The executor rejected all three and terminated safely paused.

That run neither opened the source inventory nor attempted collection. It
therefore tests planner-contract composition, not the corrected right-click
route. Its next failing invariant is:

> A plan that opens the selected-character destination must express and retain
> a causally later `open_inventory_windows == 2` condition before collection.

## Supported cleanup

The first run left a paused contextual inventory open. The old `./dev close`
correctly refused the unresolved modal, but could not reset the fresh-start test
through supported tooling.

Shutdown may now dismiss at most two windows only when complete fresh telemetry
explains them as the exact natural-resource source and selected-character
destination, with no dialogue, shop trader, context menu, active native command,
or unknown owner. It rebinds inside the input lease and requires a later
sequence with a smaller inventory-window count. Only a later paused world
permits `WM_CLOSE`.

Portable tests cover one window, both windows, and incomplete-layout zero input.
The live `./dev close` path dismissed the exact Iron Resource window, observed
the paused world, and closed Kenshi without an ad-hoc command.

## Evidence labels

- Portable automated: bind, execution-boundary, transfer-conservation, and
  lifecycle-cleanup tests.
- Supervised live Kenshi: both named run directories and the successful
  supported close.
- Not tested: a live two-window transfer, destination cleanup live, collection
  conservation, income, another resource, or another scenario.
