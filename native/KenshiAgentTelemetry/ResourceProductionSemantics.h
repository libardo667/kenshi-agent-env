#ifndef KENSHI_AGENT_RESOURCE_PRODUCTION_SEMANTICS_H
#define KENSHI_AGENT_RESOURCE_PRODUCTION_SEMANTICS_H

namespace KenshiAgentTelemetry
{
    enum ResourceProductionState
    {
        RESOURCE_PRODUCTION_OUTPUT_UNKNOWN,
        RESOURCE_PRODUCTION_APPROACHING,
        RESOURCE_PRODUCTION_WORKING,
        RESOURCE_PRODUCTION_OUTPUT_READY,
        RESOURCE_PRODUCTION_TASK_ENDED
    };

    inline ResourceProductionState EvaluateResourceProduction(
        bool outputKnown,
        int outputQuantity,
        int minimumOutputQuantity,
        bool exactTaskActive,
        bool taskObserved)
    {
        if (!outputKnown)
            return RESOURCE_PRODUCTION_OUTPUT_UNKNOWN;
        if (outputQuantity >= minimumOutputQuantity)
            return RESOURCE_PRODUCTION_OUTPUT_READY;
        if (exactTaskActive)
            return RESOURCE_PRODUCTION_WORKING;
        if (taskObserved)
            return RESOURCE_PRODUCTION_TASK_ENDED;
        return RESOURCE_PRODUCTION_APPROACHING;
    }
}

#endif
