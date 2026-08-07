"""What a character is told it can sell when its goods do not stack.

Kenshi only stacks items inside a backpack. A character carrying loose goods
gets one inventory cell per unit, and those cells share a window, a role and a
label - so they are one affordance by construction. The offer's quantity came
from a single cell, so a character holding ten raw iron was told it had one and
could sell one per turn.

That is the same duplicate-cell fact that spent an evening impersonating a
hallucinated affordance id, and it is worth a fixture rather than a live run:
window ownership needs one squad member and one shop owner, which is the whole
of the "scaffolding" I first declined to build.
"""

from __future__ import annotations

import pytest

from kenshi_agent.affordances import offered_affordances
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import ControlMode
from kenshi_agent.core.telemetry import (
    CharacterState,
    Disposition,
    GameState,
    NearbyEntity,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
)
from kenshi_agent.core.world import WorldStateRevision

MINE = "LEAF"
SHOP = "BARMAN"
ITEM = "Raw Iron"


def _cell(window: str, item: str, quantity: int, label: str = "slot") -> VisibleUIControl:
    return VisibleUIControl(
        window=window,
        role="item",
        label=label,
        item_name=item,
        item_quantity=quantity,
        item_base_value=40,
        section="inventory",
        selected_inventory_accepts_item=True,
        bounds=NormalizedPointerBounds(min_x=0.1, max_x=0.2, min_y=0.1, max_y=0.2),
    )


def _trading(*, loose_units: int, shop_units: int) -> Observation:
    """A trade screen: my loose stock on one side, the shop's on the other."""

    controls = [_cell(MINE, ITEM, 1) for _ in range(loose_units)]
    controls.append(_cell(SHOP, ITEM, shop_units, label="shopslot"))
    return Observation(
        run_id="loose-inventory",
        step_index=1,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(telemetry_sequence=7, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=7,
            identity_session_id="loose-session",
            capabilities=[
                "game.money",
                "game.pause",
                "identity.stable_handles",
                "nearby.characters",
                "nearby.shop_owners",
                "squad.basic",
                "squad.inventory",
                "ui.inventory",
                "ui.tooltip",
                "ui.visible_controls",
            ],
            game=GameState(loaded=True, paused=True, money=500),
            ui=UIState(
                active_screen="trade",
                modal_open=False,
                dialogue_open=False,
                selected_character_id="char-leaf",
                selected_character_ids=["char-leaf"],
                visible_controls=controls,
                visible_controls_complete=True,
            ),
            squad=[
                CharacterState(
                    id="char-leaf",
                    name=MINE,
                    selected=True,
                    alive=True,
                    conscious=True,
                    inventory_complete=True,
                )
            ],
            nearby_entities=[
                NearbyEntity(
                    id="char-barman",
                    name=SHOP,
                    kind="character",
                    is_animal=False,
                    shop_inventory_owner=True,
                    # A confirmed vendor is a talkable non-hostile who owns a
                    # vendor list and leads their shop squad. Every flag is
                    # explicit: the model refuses to assume a missing one.
                    has_dialogue=True,
                    disposition=Disposition.NEUTRAL,
                    has_vendor_list=True,
                    is_squad_leader=True,
                    conscious=True,
                    distance=3.0,
                )
            ],
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.05,
    )


def _offer(observation: Observation, semantic: str):  # type: ignore[no-untyped-def]
    return next(
        offer
        for offer in offered_affordances(observation)
        if offer.semantic == semantic
    )


def test_window_ownership_is_derivable_from_exported_telemetry() -> None:
    """The premise: nothing beyond a squad and a shop owner is needed."""

    owners = _trading(loose_units=10, shop_units=4).window_owners()

    assert owners[MINE.lower()]["belongs_to"] == "you"
    assert owners[SHOP.lower()]["belongs_to"] == "vendor"
    assert owners[SHOP.lower()]["seller_id"] == "char-barman"


@pytest.mark.parametrize("loose_units", [1, 3, 8])
def test_selling_counts_every_loose_cell_not_just_one(loose_units: int) -> None:
    observation = _trading(loose_units=loose_units, shop_units=4)

    offer = _offer(observation, "sell")
    quantity = next(spec for spec in offer.parameters if spec.name == "quantity")

    assert f"You have {loose_units}" in offer.description
    assert quantity.maximum == min(5, loose_units), (
        "the bound must admit everything held, not one cell"
    )


def test_buying_reports_the_shop_stock_and_asks_how_many() -> None:
    observation = _trading(loose_units=2, shop_units=4)

    offer = _offer(observation, "buy")
    quantity = next(spec for spec in offer.parameters if spec.name == "quantity")

    assert "has 4" in offer.description
    assert "How many do you want to buy?" in offer.description
    assert quantity.maximum >= 1
    assert "1 to" in quantity.description


def test_the_ten_loose_cells_are_still_one_choice() -> None:
    """Aggregating quantity must not un-collapse the duplicates."""

    offers = [
        offer
        for offer in offered_affordances(_trading(loose_units=10, shop_units=4))
        if offer.semantic == "sell"
    ]

    assert len(offers) == 1
