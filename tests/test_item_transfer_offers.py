"""What the transfer adapter offers, and what it declines to offer.

`_item_transfer_offers` had no test at all while it was the operation being
proven live, which is how a run reached a shopkeeper, advertised three carried
items, and refused all three.
"""

from __future__ import annotations

import json
from pathlib import Path

from kenshi_agent.affordances import enumerate_affordance_set, offered_affordances
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import ControlMode
from kenshi_agent.core.telemetry import (
    CharacterState,
    GameState,
    InventorySectionView,
    InventorySlotItem,
    OpenInventory,
    TelemetrySnapshot,
    UIState,
    Vec3,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_definitions import NATIVE_TRANSFER_CAPABILITY


def _item(name: str, x: int, y: int) -> InventorySlotItem:
    return InventorySlotItem(
        item_name=name,
        item_sell_value=10,
        item_base_value=10,
        item_quantity=1,
        item_type=1,
        x=x,
        y=y,
        w=1,
        h=1,
    )


def _inventory(
    owner_id: str,
    owner_name: str,
    *,
    player_owned: bool,
    sections: list[InventorySectionView],
    within_trade_range: bool | None = True,
) -> OpenInventory:
    return OpenInventory(
        owner_id=owner_id,
        owner_name=owner_name,
        owner_kind="character",
        player_owned=player_owned,
        money=1000,
        total_weight=1.0,
        sections=sections,
        within_trade_range=within_trade_range,
    )


def _observation(inventories: list[OpenInventory]) -> Observation:
    return Observation(
        run_id="transfer-offer-test",
        step_index=1,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(telemetry_sequence=7, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=7,
            identity_session_id="session-transfer-offer-test",
            capabilities=[
                NATIVE_TRANSFER_CAPABILITY,
                "identity.stable_handles",
                "ui.inventory",
            ],
            game=GameState(loaded=True, paused=True, speed_multiplier=1.0),
            roster=[
                CharacterState(
                    id="entity-fish",
                    name="Fish",
                    alive=True,
                    conscious=True,
                    down=False,
                    position=Vec3(x=0, y=0, z=0),
                )
            ],
                ui=UIState(
                    active_screen="inventory",
                    modal_open=True,
                    dialogue_open=False,
                    open_inventories=inventories,
                    open_inventories_complete=True,
                ),
                primary_character_id="entity-fish",
                selected_character_ids=["entity-fish"],
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.1,
    )


def _transfer_semantics(inventories: list[OpenInventory]) -> set[str]:
    return {
        offer.semantic
        for offer in offered_affordances(_observation(inventories))
        if offer.operation_kind == "transfer_item"
    }


def _transfer_offers(inventories: list[OpenInventory]):
    return [
        offer
        for offer in offered_affordances(_observation(inventories))
        if offer.operation_kind == "transfer_item"
    ]


def _carried(*items: InventorySlotItem) -> list[InventorySectionView]:
    return [
        InventorySectionView(
            name="backpack_content", equipped=False, width=20, height=20, items=list(items)
        )
    ]


def _fish(**kwargs: object) -> OpenInventory:
    return _inventory(
        "entity-fish",
        "Fish",
        player_owned=True,
        sections=_carried(_item("Rice Bowl", 0, 0)),
        **kwargs,  # type: ignore[arg-type]
    )


def _barman(**kwargs: object) -> OpenInventory:
    return _inventory(
        "entity-barman",
        "Barman",
        player_owned=False,
        sections=_carried(_item("Water", 6, 4), _item("Foodcube", 14, 5)),
        **kwargs,  # type: ignore[arg-type]
    )


def test_both_directions_are_offered_between_two_reachable_inventories() -> None:
    semantics = _transfer_semantics([_fish(), _barman()])

    assert semantics == {"transfer_item"}


def test_reach_is_what_removes_the_offers_rather_than_an_empty_world() -> None:
    """Asserted against the same pair in reach, so silence cannot pass this.

    Both halves of this ran green while the fixture was building no offers at
    all, which is the only reason they are written as a difference.
    """

    in_reach = _transfer_offers([_fish(), _barman()])
    assert len(in_reach) == 3

    out_of_reach = _transfer_offers(
        [_fish(), _barman(within_trade_range=False)]
    )
    assert out_of_reach == []


def test_unknown_reach_is_silence_rather_than_a_denial() -> None:
    offers = _transfer_offers(
        [_fish(within_trade_range=None), _barman(within_trade_range=None)]
    )

    assert len(offers) == 3


def test_worn_gear_is_offered_because_a_body_is_mostly_wearing_it() -> None:
    """Equipped sections are transferable, and hiding them broke looting.

    This replaces a test asserting the opposite. That refusal was inherited
    from a crash blamed on equipment, which turned out to be a return-convention
    mismatch that crashed every transfer regardless of section. Measured live:
    an unconscious character offered a Katana and ragged Halfpants, both worn,
    and nothing else -- so refusing worn gear refused the whole corpse.
    """

    armed = _inventory(
        "entity-burn",
        "Burn",
        player_owned=False,
        sections=[
            InventorySectionView(
                name="back", equipped=True, width=2, height=2, items=[_item("Katana", 0, 0)]
            ),
            InventorySectionView(
                name="legs",
                equipped=True,
                width=4,
                height=4,
                items=[_item("Halfpants", 0, 0)],
            ),
        ],
    )

    offers = _transfer_offers([_fish(), armed])

    assert {offer.operation_arguments["item_name"] for offer in offers} >= {
        "Katana",
        "Halfpants",
    }

def test_identically_named_items_stay_distinguishable_by_slot() -> None:
    hoarder = _inventory(
        "entity-hoarder",
        "Hoarder",
        player_owned=False,
        sections=_carried(_item("Water", 1, 1), _item("Water", 9, 9)),
    )

    offers = _transfer_offers([_fish(), hoarder])
    water_offers = [
        offer for offer in offers if offer.operation_arguments["item_name"] == "Water"
    ]

    assert len(water_offers) == 2
    assert len({offer.target.target_id for offer in water_offers if offer.target}) == 2


def test_affordance_set_keeps_transfer_mechanics_private() -> None:
    enumeration = enumerate_affordance_set(_observation([_fish(), _barman()]))
    event = enumeration.as_evidence(context_id="pc-1")
    transfers = [offer for offer in event.offers if offer.operation_kind == "transfer_item"]

    assert len(transfers) == 3
    assert all(offer.semantic == "transfer_item" for offer in transfers)
    assert all(offer.selection_target_id.startswith("inventory-transfer-") for offer in transfers)
    assert all(
        {target.role for target in offer.applicable_targets}
        == {"source_inventory", "destination_inventory"}
        for offer in transfers
    )
    serialized = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    for forbidden in (
        "slot_x",
        "slot_y",
        "section_name",
        "backpack_content",
        "description",
        "label",
        "operation_arguments",
    ):
        assert forbidden not in serialized


def test_native_transfer_authority_is_the_inventory_model_not_auto_trade() -> None:
    """Pin the call sites behind the proof-ledger description."""

    source = (
        Path(__file__).resolve().parents[1]
        / "native"
        / "KenshiAgentTelemetry"
        / "KenshiAgentTelemetry.cpp"
    ).read_text(encoding="utf-8")
    transfer = source[
        source.index("        if (isTransfer)\n") : source.index(
            "        if (isContextAction || isResourceProduction)\n"
        )
    ]

    assert "InventorySection* section" in transfer
    assert "section->getItemAt" in transfer
    assert "removeItemDontDestroy_returnsItem" in transfer
    assert "destinationInventory->tryAddItem" in transfer
    assert "InventoryGUI::getNPCTrader" in transfer
    assert "item->getValueSingle" in transfer
    assert "payer->takeMoney" in transfer
    assert "RClickAutoTrade(" not in transfer
