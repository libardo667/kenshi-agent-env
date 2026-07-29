#pragma once

namespace KenshiAgentTelemetry
{
    inline bool IsTradeInventoryOpen(
        bool anyInventoryOpen,
        bool traderInventoryOpen)
    {
        return anyInventoryOpen && traderInventoryOpen;
    }
}
