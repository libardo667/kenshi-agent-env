from __future__ import annotations

from kenshi_agent.action_contracts import PURCHASE_ITEM_CONTRACT, SELL_ITEM_CONTRACT
from kenshi_agent.config import load_config
from kenshi_agent.live_dev import _telemetry_read
from kenshi_agent.models import (
    ControlMode,
    Observation,
    PurchaseItemAction,
    SellItemAction,
    WorldStateRevision,
)


def main() -> int:
    snapshot = _telemetry_read(load_config("config/live.longform.yaml")).read().snapshot
    obs = Observation(
        run_id="probe", step_index=0, mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(telemetry_sequence=snapshot.sequence),
        telemetry=snapshot, telemetry_stale=False, objective="probe",
    )
    ui = snapshot.ui
    cells = [c for c in (ui.visible_controls or []) if c.role == "item"]
    texts = [c for c in (ui.visible_controls or []) if c.role == "text"]
    owners = [e for e in snapshot.nearby_entities if e.shop_inventory_owner]
    selected = next((c for c in snapshot.squad if c.selected), None)

    print(f"screen={ui.active_screen} inv_windows={ui.open_inventory_windows} "
          f"money={snapshot.game.money}")
    print(f"total controls={len(ui.visible_controls or [])} items={len(cells)} text={len(texts)}")
    print(f"cell windows: {sorted({c.window for c in cells if c.window})}")
    print(f"selected={selected.name if selected else None} "
          f"owners={[e.name for e in owners]}")
    named = [c for c in cells if c.item_name and c.item_value]
    print(f"cells naming themselves: {len(named)}/{len(cells)}")
    if not cells or not owners or selected is None:
        return 1

    seller = owners[0]
    print("\n--- purchase_item (seller's stock) ---")
    shop_cells = [c for c in named if c.window and c.window.casefold() != (selected.name or "").casefold()]
    for cell in shop_cells[:5]:
        a = PurchaseItemAction(cell_label=cell.label, item_name=cell.item_name,
                               expected_price=cell.item_value, window=cell.window,
                               seller_id=seller.id)
        b = PURCHASE_ITEM_CONTRACT.bind(a, obs)
        print(f"  {'BINDS ' if b.bound else 'refuse'} {cell.item_name!r} c.{cell.item_value} [{cell.window}]")
        if not b.bound:
            print(f"         {b.reason}")

    print("\n--- sell_item (our stock) ---")
    our_cells = [c for c in named if c.window and c.window.casefold() == (selected.name or "").casefold()]
    for cell in our_cells[:5]:
        a = SellItemAction(cell_label=cell.label, item_name=cell.item_name,
                           window=cell.window, buyer_id=seller.id)
        b = SELL_ITEM_CONTRACT.bind(a, obs)
        print(f"  {'BINDS ' if b.bound else 'refuse'} {cell.item_name!r} [{cell.window}]")
        if not b.bound:
            print(f"         {b.reason}")

    print("\n--- text the agent can now read ---")
    for c in texts[:8]:
        print(f"  [{c.window}] {c.label[:70]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
