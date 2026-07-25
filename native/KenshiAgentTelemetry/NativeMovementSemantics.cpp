#include "NativeMovementSemantics.h"

namespace KenshiAgentTelemetry
{
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
}
