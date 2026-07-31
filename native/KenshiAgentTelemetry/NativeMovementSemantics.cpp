#include "NativeMovementSemantics.h"

#include <cmath>

namespace KenshiAgentTelemetry
{
    bool TryComputeTrailingCameraPose(
        float originX,
        float originZ,
        float destinationX,
        float destinationZ,
        float motionX,
        float motionZ,
        float currentZoom,
        NativeTrailingCameraPose& pose)
    {
        float dx = motionX;
        float dz = motionZ;
        const float horizontalLength =
            static_cast<float>(std::sqrt(dx * dx + dz * dz));
        float headingLength = horizontalLength;
        if (headingLength <= 0.001f)
        {
            dx = destinationX - originX;
            dz = destinationZ - originZ;
            headingLength =
                static_cast<float>(std::sqrt(dx * dx + dz * dz));
            if (headingLength <= 0.001f)
                return false;
        }

        // The live heading wins over the final waypoint so a curved navmesh
        // route rotates the view with the character instead of pinning it to
        // one world vector. The destination is only the startup/idle fallback.
        const float headingX = dx / headingLength;
        const float headingZ = dz / headingLength;
        const float downwardPitch = 0.35f;
        const float downward = -static_cast<float>(std::sin(downwardPitch));
        const float facingLength =
            static_cast<float>(std::cos(downwardPitch));
        pose.facingX = headingX * facingLength;
        pose.facingY = downward;
        pose.facingZ = headingZ * facingLength;

        // CameraClass::manuallySetOrientationAndZoom rotates Kenshi's center
        // node using yaw(Y) * pitch(X). KenshiFP independently live-proved this
        // exact handoff convention; a nominal Ogre -Z shortest-arc quaternion
        // instead pitches the RTS camera into the sky.
        const float yaw =
            static_cast<float>(std::atan2(headingX, headingZ));
        const float halfYaw = yaw * 0.5f;
        const float halfPitch = downwardPitch * 0.5f;
        const float cosYaw = static_cast<float>(std::cos(halfYaw));
        const float sinYaw = static_cast<float>(std::sin(halfYaw));
        const float cosPitch = static_cast<float>(std::cos(halfPitch));
        const float sinPitch = static_cast<float>(std::sin(halfPitch));
        pose.w = cosYaw * cosPitch;
        pose.x = cosYaw * sinPitch;
        pose.y = sinYaw * cosPitch;
        pose.z = -sinYaw * sinPitch;

        // Kenshi places a positive manual zoom on the front side of the
        // followed object. Preserve the useful distance magnitude, but make it
        // negative so the camera is actually behind the character.
        float zoomMagnitude =
            currentZoom < 0.0f ? -currentZoom : currentZoom;
        if (zoomMagnitude < 20.0f)
            zoomMagnitude = 20.0f;
        if (zoomMagnitude > 60.0f)
            zoomMagnitude = 60.0f;
        pose.zoom = -zoomMagnitude;
        return true;
    }

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

    bool IsNativeMapDestinationPresentlyReached(
        bool currentTownIdentityMatches,
        bool destinationHasGates,
        bool insideTownWalls)
    {
        return currentTownIdentityMatches &&
               (!destinationHasGates || insideTownWalls);
    }

    NativeMapTravelDecision EvaluateNativeMapTravel(
        bool currentTownIdentityMatches,
        bool destinationHasGates,
        bool insideTownWalls,
        bool currentLegReached,
        bool interiorLegIssued)
    {
        const bool destinationReached =
            IsNativeMapDestinationPresentlyReached(
                currentTownIdentityMatches,
                destinationHasGates,
                insideTownWalls);
        if (!destinationHasGates && destinationReached)
            return MAP_TRAVEL_COMPLETE;
        if (!currentLegReached)
            return MAP_TRAVEL_CONTINUE;
        if (!interiorLegIssued)
            return MAP_TRAVEL_ISSUE_INTERIOR_ORDER;
        return currentTownIdentityMatches
            ? MAP_TRAVEL_COMPLETE
            : MAP_TRAVEL_CANCEL_UNCONFIRMED;
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
