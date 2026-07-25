#pragma once

namespace KenshiAgentTelemetry
{
    const float WALK_DESTINATION_TOLERANCE = 12.0f;

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
}
