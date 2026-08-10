#pragma once

namespace KenshiAgentTelemetry
{
    const float TRADE_WINDOW_AUTHORING_DISTANCE = 30.0f;

    inline bool IsTradePairWithinAuthoringDistance(
        float deltaX,
        float deltaY,
        float deltaZ)
    {
        const float distanceSquared =
            deltaX * deltaX + deltaY * deltaY + deltaZ * deltaZ;
        return distanceSquared <=
            TRADE_WINDOW_AUTHORING_DISTANCE *
                TRADE_WINDOW_AUTHORING_DISTANCE;
    }

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
