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

    enum ResourceTaskReleaseState
    {
        RESOURCE_TASK_RELEASE_NOT_OWNED,
        RESOURCE_TASK_RELEASE_REQUESTED,
        RESOURCE_TASK_RELEASE_WAITING,
        RESOURCE_TASK_RELEASE_CONFIRMED
    };

    const unsigned long RESOURCE_TASK_RELEASE_CONFIRMATION_MS = 1000UL;

    struct ResourceTaskReleaseConfirmationWindow
    {
        bool trackingInactive;
        unsigned long inactiveSinceMs;
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

    inline ResourceTaskReleaseState EvaluateResourceTaskRelease(
        bool issuedByCommand,
        bool releaseRequested,
        bool inactiveConfirmed)
    {
        if (!issuedByCommand)
            return RESOURCE_TASK_RELEASE_NOT_OWNED;
        if (!releaseRequested)
            return RESOURCE_TASK_RELEASE_REQUESTED;
        return inactiveConfirmed
            ? RESOURCE_TASK_RELEASE_CONFIRMED
            : RESOURCE_TASK_RELEASE_WAITING;
    }

    inline void ResetResourceTaskReleaseConfirmationWindow(
        ResourceTaskReleaseConfirmationWindow& window)
    {
        window.trackingInactive = false;
        window.inactiveSinceMs = 0UL;
    }

    inline bool ObserveResourceTaskReleaseConfirmation(
        ResourceTaskReleaseConfirmationWindow& window,
        bool releaseStillActive,
        unsigned long nowMs)
    {
        if (releaseStillActive)
        {
            ResetResourceTaskReleaseConfirmationWindow(window);
            return false;
        }
        if (!window.trackingInactive)
        {
            window.trackingInactive = true;
            window.inactiveSinceMs = nowMs;
            return false;
        }
        return nowMs - window.inactiveSinceMs >=
            RESOURCE_TASK_RELEASE_CONFIRMATION_MS;
    }
}

#endif
