#pragma once

namespace KenshiAgentTelemetry
{
    inline bool IsRegisteredShopInventoryOpen(
        bool ownerCharacterInventoryOpen,
        bool shopInventoryObjectOpen)
    {
        // Kenshi keys a shop's stock window by its ShopTrader object, not by
        // the character who owns that object. The character's ordinary
        // equipment window must never grant trade authority.
        (void)ownerCharacterInventoryOpen;
        return shopInventoryObjectOpen;
    }

    inline bool IsTradeInventoryOpen(
        bool anyInventoryOpen,
        bool transientTraderInventoryOpen,
        bool registeredShopInventoryOpen)
    {
        return anyInventoryOpen &&
            (transientTraderInventoryOpen || registeredShopInventoryOpen);
    }
}
