# Guide: building the Ladle game start

Ladle v1 is a small, clean Game Start rather than a full delivery quest system.
Division of labor:

- **FCS** creates Ladle, his equipment, his sealed cargo, starting money, and
  starting location.
- **The agent objective** names the destination and defines what counts as
  successful delivery.
- **Later**, a recipient NPC can inspect/remove the cargo and pay Ladle through
  dialogue.

A Game Start suits the first part: it can define the starting squad, money, town
or coordinates, relations, research, and race restrictions, while the character
template carries stats, equipment, inventory, health, and related state.

## The Ladle specification

| Record | Name |
| --- | --- |
| Mod | `LadleDelivery` |
| Cargo item | `CSS - Sealed Slop Canister` |
| Stats | `CSS - Rookie Slop Courier` |
| Character | `CSS - Ladle` |
| Squad | `startoff - CSS Ladle` |
| Smoke-test start | `Ladle - Local Route` |
| Final start | `KAE 05 - Ladle Long Haul` |

Ladle himself: Western Hive Worker Drone; 20 Cats; Wooden Backpack; six sealed
slop canisters; two Dried Meat; one Basic First Aid Kit; ragged legwear; a
Leather Hive Vest or no shirt; no serious weapon, perhaps an Iron Stick;
Athletics 5, Labouring 10, Strength and Toughness 2, combat skills 1.

The Wooden Backpack is particularly appropriate because it stacks trade goods
but penalizes combat, stealth, and dodge. It is a genuine freight bag rather
than adventuring equipment.

Do not give Ladle footwear. Hivers cannot use the footwear slot, and their shirt
slot only accepts certain Hiver clothing such as the Leather Hive Vest.

## 1. Create an isolated mod

Close Kenshi and launch `forgotten construction set.exe`. Create a new mod file
named `LadleDelivery`. Keep the core game files checked; uncheck unrelated local
or Workshop mods for now. Confirm `LadleDelivery` is marked `*ACTIVE*` before
selecting DONE.

The active mod is where FCS writes changes. Creating copies instead of editing
vanilla records keeps the new start isolated and prevents accidental changes to
shared game data.

Do not edit the installed `KenshiAgentScenarios.mod` yet: the repository
verifies it by exact byte length and SHA-256 digest, so even a legitimate FCS
save would make `verify-starts` reject it until the manifest was deliberately
updated.

## 2. Make the sealed slop

Locate the vanilla item `Grog` (the search box is easier than the category
tree). Right-click → Duplicate Item. Rename the copy
`CSS - Sealed Slop Canister` and give it a description resembling:

> A sealed canister of industrial food paste. Property of Continuous Slop
> Solutions. Delivery contents are not to be sampled, diluted, substituted, or
> explained.

Leave its visual, inventory, and trade-good references intact. Grog gives a
barrel-like, non-food trade good without needing an icon or mesh, and — most
importantly — Ladle will not automatically eat the shipment.

Adjust if clearly presented: weight around 4, base value around 100,
stackability inherited, charges/nutrition inherited.

Do not manually rewrite the String ID. FCS-generated identifiers are how
references between objects stay intact; duplicating gives a separate record
safely.

## 3. Make Ladle's stats

Under Characters → Stats, find a weak or ordinary starting-stat record — one
used by the Wanderer start is a sensible base. Duplicate it as
`CSS - Rookie Slop Courier`.

```
Athletics          5
Labouring         10
Strength           2
Toughness          2
Field Medic        1
Melee Attack       1
Melee Defence      1
Dodge              1
All weapon skills  1
Everything else    1
```

This gives Ladle one legitimate professional competency — moving things around —
without turning him into an adventurer wearing a courier costume. Raise
Athletics to 10 if the first agent run is excruciatingly slow; start at 5 so
route survival and encumbrance stay meaningful.

## 4. Make Ladle

Open Characters, find `Wanderer`, right-click → Duplicate Item, rename
`CSS - Ladle`. Duplicating Wanderer preserves the less-obvious defaults and
player dialogue configuration.

In Ladle's record, use the reference dropdown in the upper-right area:

- **Race** — add `Hive Worker Drone`; remove any inherited Greenlander,
  Scorchlander, or other race.
- **Stats** — add `CSS - Rookie Slop Courier`; remove the Wanderer package.
- **Inventory** — `CSS - Sealed Slop Canister` ×6, `Dried Meat` ×2,
  `Basic First Aid Kit` ×1. Surplus goes to an equipped backpack if space
  permits.
- **Backpack** — `Wooden Backpack`.
- **Clothing** — Halfpants (ragged) and, optionally, a Leather Hive Vest. No
  boots.
- **Weapon** — remove the inherited weapon, or replace it with an `Iron Stick`:
  less a weapon than evidence that Continuous Slop Solutions complied with its
  employee-safety policy.

Save the record.

## 5. Make Ladle's squad

Faction → Squads. Find the Wanderer starting squad (close to
`startoff - Wanderer squad`), duplicate it, rename `startoff - CSS Ladle`.
Remove the Wanderer leader, add `CSS - Ladle` as leader, and make sure there are
no additional members, animals, slaves, or mercenaries. Save.

## 6. Make a local smoke-test Game Start

Under Game Starts, duplicate `Wanderer` and rename `Ladle - Local Route`:

> Ladle is the sole remaining employee of Continuous Slop Solutions. Six sealed
> canisters must reach their destination. The delivery fee will probably not
> cover medical expenses.

- Starting money: 20
- Starting squad: replace Wanderer's with `startoff - CSS Ladle`
- Starting town: retain or set The Hub
- Force race: add only `Hive Worker Drone`
- Leave inherited default research entries alone

Remove old town or squad references after adding the new ones. Multiple town
entries can scatter the start location, so a reproducible test wants precisely
one. Save the mod with the toolbar's save button.

## 7. Test the local version

Close FCS, launch Kenshi, enable `LadleDelivery` in the launcher, then
New Game → `Ladle - Local Route`. Leave the default name and appearance.

Verify: Hive Worker Drone; name Ladle; party size one; exactly 20 Cats; Wooden
Backpack equipped; six slop canisters; personal food and medical supplies
separately; no inherited Wanderer equipment; starts at The Hub.

Canonical appearance can wait for an exported `.bod2`; random little Ladle is
enough to validate the behavioral experiment. First agent test objective:

> Deliver all six sealed slop canisters from The Hub to Squin. Preserve the
> complete shipment. Survival takes priority over speed. You may buy supplies,
> rest, retreat, hire help, fight, or reroute as needed. Do not sell, discard,
> or consume the shipment.

That route exercises cargo preservation, town finding, pathing, and threat
response without immediately making swamp fauna the principal investigator.

## 8. Create the real long-haul version

Duplicate `Ladle - Local Route` as `KAE 05 - Ladle Long Haul` and change only
the starting town to Sho-Battai. Canonical contract: **Sho-Battai → Shark**,
which crosses enough of Kenshi to create actual logistical judgment — food,
money, route selection, hostile encounters, injuries, rest, encumbrance, towns,
desert, and swamp.

> You are Ladle, the sole employee of Continuous Slop Solutions. Deliver all six
> sealed slop canisters from Sho-Battai to Shark. Keep the shipment intact.
> Survival and eventual delivery take priority over speed. You may trade other
> property, purchase supplies, hire protection, rest, heal, retreat, fight, or
> select a different route. Do not sell, discard, or abandon the cargo unless
> delivery has become genuinely impossible; if that happens, preserve yourself
> and record why the contract failed. Stop only after reaching Shark and
> verifying that all six canisters remain under your control.

That is a strong continuous-agent objective: it establishes a persistent
invariant — six canisters — while leaving almost every tactical decision open.

## 9. Fold Ladle into kenshi-agent-env

Keep the standalone `LadleDelivery` mod until the start is proven. Then merge or
duplicate the Ladle records into `KenshiAgentScenarios` with that mod active in
FCS, and replace
`scenarios/KenshiAgentScenarios/KenshiAgentScenarios.mod`.

Add the manifest entry:

```json
{
  "start_id": "kae-05-ladle-long-haul",
  "label": "KAE 05 - Ladle Long Haul",
  "money": 20,
  "party_size": 1
}
```

Recalculate the binary's length and hash in PowerShell:

```powershell
$mod = "scenarios\KenshiAgentScenarios\KenshiAgentScenarios.mod"

(Get-Item $mod).Length
(Get-FileHash $mod -Algorithm SHA256).Hash.ToLower()
```

Put those into the manifest's `size_bytes` and `sha256`. Also update
`tests/test_authored_starts.py`, whose checked-in bundle test currently expects
exactly four start IDs and four money/party-size pairs.

With Kenshi and FCS closed:

```bash
./dev scenario install-starts
./dev scenario verify-starts
./dev launch --game-start kae-05-ladle-long-haul
```

The tooling installs and verifies the exact bundled mod, selects an authored
start by its rendered FCS label, and proves the expected Cats and party size
from fresh telemetry.

At that point Ladle is a reproducible, exact-start, cargo-preservation benchmark
for continuous planning — who happens to be transporting six cans of deeply
questionable paste through one of the least employment-law-compliant worlds
imaginable.
