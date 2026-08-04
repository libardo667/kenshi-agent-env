#pragma once

#include <vector>

namespace KenshiAgentTelemetry
{
    struct NativeMovementPosition
    {
        float x;
        float z;
    };

    struct NativeTrailingCameraPose
    {
        float w;
        float x;
        float y;
        float z;
        float facingX;
        float facingY;
        float facingZ;
        float zoom;
    };

    bool TryComputeTrailingCameraPose(
        float originX,
        float originZ,
        float destinationX,
        float destinationZ,
        float motionX,
        float motionZ,
        float currentZoom,
        NativeTrailingCameraPose& pose);

    const float WALK_DESTINATION_TOLERANCE = 12.0f;
    const float NATIVE_MOVEMENT_PROGRESS_DISTANCE = 1.0f;
    const float NATIVE_EXIT_DESTINATION_TOLERANCE = 3.0f;
    const unsigned long NATIVE_MOVEMENT_STALL_LIMIT_MS = 10000UL;
    const unsigned long NATIVE_OUTDOOR_CONFIRMATION_MS = 500UL;

    struct NativeMovementStallWindow
    {
        bool observing;
        unsigned long lastProgressAtMs;
        float lastProgressX;
        float lastProgressZ;
    };

    struct NativeOutdoorConfirmationWindow
    {
        bool observingOutside;
        unsigned long startedAtMs;
    };

    void ResetNativeMovementStallWindow(NativeMovementStallWindow& window);

    // A continuous unpaused interval with less than one world unit of progress
    // is a terminal pathing stall. Paused stop-motion gaps reset the interval
    // rather than counting human/controller thinking time as a movement fault.
    bool ObserveNativeMovementStall(
        NativeMovementStallWindow& window,
        bool worldPaused,
        float currentX,
        float currentZ,
        unsigned long nowMs);

    void ResetNativeOutdoorConfirmationWindow(
        NativeOutdoorConfirmationWindow& window);

    // A hand can remain valid after its building no longer resolves. Indoor
    // membership and exit authority must use this same fail-closed predicate.
    bool HasResolvedIndoorBuilding(
        bool handleValid,
        bool buildingExists,
        bool buildingValid);

    // Building handles can change while traversing nested interior/door
    // objects. Completion requires remaining outside every building for one
    // telemetry interval, not merely observing a different valid handle.
    bool ObserveNativeOutdoorConfirmation(
        NativeOutdoorConfirmationWindow& window,
        bool indoors,
        unsigned long nowMs);

    // Kenshi can retain an indoor building handle after the character has
    // visibly crossed an open doorway. The native-resolved outside point is a
    // second authoritative terminal: it must be reached tightly, and the
    // character must have made real progress from the indoor origin.
    bool HasReachedResolvedExitDestination(
        float originX,
        float originZ,
        float destinationX,
        float destinationZ,
        float currentX,
        float currentZ);

    // A bare directional destination is reached either inside the ordinary
    // walk tolerance or after the character crosses the destination plane
    // along the requested vector. The latter recognizes a legitimate navmesh
    // detour/overshoot without treating sideways or short blocked movement as
    // completion.
    bool HasReachedFixedDirectionDestination(
        float originX,
        float originZ,
        float destinationX,
        float destinationZ,
        float currentX,
        float currentZ);

    // A group walk owns every selected member. It reaches one shared
    // destination only when every member is inside the ordinary walk radius;
    // the farthest member also owns stall progress so the leader cannot mask
    // a stuck party. Unlike single-character directional completion, a member
    // far beyond the destination plane is not an arrival.
    bool HasGroupReachedDestination(
        const std::vector<NativeMovementPosition>& positions,
        float destinationX,
        float destinationZ,
        float& farthestX,
        float& farthestZ);

    enum NativeMapTravelDecision
    {
        MAP_TRAVEL_CONTINUE,
        MAP_TRAVEL_ISSUE_INTERIOR_ORDER,
        MAP_TRAVEL_COMPLETE,
        MAP_TRAVEL_CANCEL_UNCONFIRMED
    };

    // Present-tense "already reached" remains conservative at issue time: a
    // gated town additionally requires an inside-walls observation. Ungated
    // travel may also use this predicate as its immediate terminal.
    bool IsNativeMapDestinationPresentlyReached(
        bool currentTownIdentityMatches,
        bool destinationHasGates,
        bool insideTownWalls);

    // Map markers resolve to direction-dependent gate waypoints. A gated town
    // must finish the controller-owned interior leg. Once that exact leg
    // reaches its native-resolved endpoint, exact town identity proves arrival;
    // some valid town geometry never exposes an inside-walls state.
    NativeMapTravelDecision EvaluateNativeMapTravel(
        bool currentTownIdentityMatches,
        bool destinationHasGates,
        bool insideTownWalls,
        bool currentLegReached,
        bool interiorLegIssued);

    // Camera ownership follows native command ownership. Reassert follow only
    // while one command is active and the current exact selection still owns
    // that command; any ambiguity or selection drift fails closed.
    bool ShouldMaintainCameraFollow(
        bool commandActive,
        bool exactSelectionResolved,
        bool selectionIdentityMatches);
}
