#include "NativeMovementSemantics.h"

namespace KenshiAgentTelemetry
{
    void ResetNativeMovementStallWindow(NativeMovementStallWindow& window)
    {
        window.observing = false;
        window.lastProgressAtMs = 0UL;
        window.lastProgressX = 0.0f;
        window.lastProgressZ = 0.0f;
    }

    bool ObserveNativeMovementStall(
        NativeMovementStallWindow& window,
        bool worldPaused,
        float currentX,
        float currentZ,
        unsigned long nowMs)
    {
        if (worldPaused)
        {
            ResetNativeMovementStallWindow(window);
            return false;
        }
        if (!window.observing)
        {
            window.observing = true;
            window.lastProgressAtMs = nowMs;
            window.lastProgressX = currentX;
            window.lastProgressZ = currentZ;
            return false;
        }

        const float dx = currentX - window.lastProgressX;
        const float dz = currentZ - window.lastProgressZ;
        if (dx * dx + dz * dz >=
            NATIVE_MOVEMENT_PROGRESS_DISTANCE *
                NATIVE_MOVEMENT_PROGRESS_DISTANCE)
        {
            window.lastProgressAtMs = nowMs;
            window.lastProgressX = currentX;
            window.lastProgressZ = currentZ;
            return false;
        }
        return nowMs - window.lastProgressAtMs >=
            NATIVE_MOVEMENT_STALL_LIMIT_MS;
    }

    void ResetNativeOutdoorConfirmationWindow(
        NativeOutdoorConfirmationWindow& window)
    {
        window.observingOutside = false;
        window.startedAtMs = 0UL;
    }

    bool HasResolvedIndoorBuilding(
        bool handleValid,
        bool buildingExists,
        bool buildingValid)
    {
        return handleValid && buildingExists && buildingValid;
    }

    bool ObserveNativeOutdoorConfirmation(
        NativeOutdoorConfirmationWindow& window,
        bool indoors,
        unsigned long nowMs)
    {
        if (indoors)
        {
            ResetNativeOutdoorConfirmationWindow(window);
            return false;
        }
        if (!window.observingOutside)
        {
            window.observingOutside = true;
            window.startedAtMs = nowMs;
            return false;
        }
        return nowMs - window.startedAtMs >= NATIVE_OUTDOOR_CONFIRMATION_MS;
    }

    bool HasReachedResolvedExitDestination(
        float originX,
        float originZ,
        float destinationX,
        float destinationZ,
        float currentX,
        float currentZ)
    {
        const float progressX = currentX - originX;
        const float progressZ = currentZ - originZ;
        if (progressX * progressX + progressZ * progressZ <
            NATIVE_MOVEMENT_PROGRESS_DISTANCE *
                NATIVE_MOVEMENT_PROGRESS_DISTANCE)
        {
            return false;
        }

        const float remainingX = currentX - destinationX;
        const float remainingZ = currentZ - destinationZ;
        return remainingX * remainingX + remainingZ * remainingZ <=
            NATIVE_EXIT_DESTINATION_TOLERANCE *
                NATIVE_EXIT_DESTINATION_TOLERANCE;
    }

    bool HasReachedFixedDirectionDestination(
        float originX,
        float originZ,
        float destinationX,
        float destinationZ,
        float currentX,
        float currentZ)
    {
        const float remainingX = currentX - destinationX;
        const float remainingZ = currentZ - destinationZ;
        if (remainingX * remainingX + remainingZ * remainingZ <=
            WALK_DESTINATION_TOLERANCE * WALK_DESTINATION_TOLERANCE)
        {
            return true;
        }

        const float goalX = destinationX - originX;
        const float goalZ = destinationZ - originZ;
        const float goalLengthSquared = goalX * goalX + goalZ * goalZ;
        if (goalLengthSquared <= 0.0f)
            return false;

        const float progressX = currentX - originX;
        const float progressZ = currentZ - originZ;
        return progressX * goalX + progressZ * goalZ >= goalLengthSquared;
    }

    bool ShouldMaintainCameraFollow(
        bool commandActive,
        bool exactSelectionResolved,
        bool selectionIdentityMatches)
    {
        return commandActive &&
               exactSelectionResolved &&
               selectionIdentityMatches;
    }
}
