#pragma once

namespace KenshiAgentTelemetry
{
    inline bool IsTradeInventoryOpen(
        bool anyInventoryOpen,
        bool transientTraderInventoryOpen,
        bool registeredShopInventoryOpen)
    {
        return anyInventoryOpen &&
            (transientTraderInventoryOpen || registeredShopInventoryOpen);
    }
}
