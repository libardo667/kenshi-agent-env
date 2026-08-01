#pragma once

#include <string>
#include <vector>

namespace KenshiAgentTelemetry
{
    enum RuntimeContextMenuProbe
    {
        RUNTIME_CONTEXT_MENU_CLOSED,
        RUNTIME_CONTEXT_MENU_CAPTURED,
        RUNTIME_CONTEXT_MENU_INVALID_TARGET
    };

    struct RuntimeContextMenuTargetOwnership
    {
        bool captured;
        std::string targetId;
        std::string targetName;

        RuntimeContextMenuTargetOwnership()
            : captured(false)
        {
        }
    };

    inline void UpdateRuntimeContextMenuTargetOwnership(
        RuntimeContextMenuTargetOwnership& ownership,
        bool showing,
        bool targetValid,
        const std::string& targetId,
        const std::string& targetName)
    {
        ownership.captured = showing && targetValid && !targetId.empty();
        if (ownership.captured)
        {
            ownership.targetId = targetId;
            ownership.targetName = targetName;
        }
        else
        {
            ownership.targetId.clear();
            ownership.targetName.clear();
        }
    }

    struct RuntimeContextMenuObservation
    {
        bool open;
        bool captured;
        RuntimeContextMenuProbe probe;
        std::string targetId;
        std::string targetName;
        std::vector<int> taskTypeValues;
        bool taskTypeValuesComplete;

        RuntimeContextMenuObservation()
            : open(false),
              captured(false),
              probe(RUNTIME_CONTEXT_MENU_CLOSED),
              taskTypeValuesComplete(false)
        {
        }
    };

    inline RuntimeContextMenuObservation ResolveRuntimeContextMenu(
        bool reportedOpen,
        const RuntimeContextMenuTargetOwnership& ownership,
        const std::vector<int>& taskTypeValues,
        unsigned int maximumTaskTypeValues)
    {
        RuntimeContextMenuObservation observation;
        if (!reportedOpen)
            return observation;

        observation.open = true;
        if (!ownership.captured)
        {
            observation.probe = RUNTIME_CONTEXT_MENU_INVALID_TARGET;
            return observation;
        }

        observation.captured = true;
        observation.probe = RUNTIME_CONTEXT_MENU_CAPTURED;
        observation.targetId = ownership.targetId;
        observation.targetName = ownership.targetName;
        const unsigned int available =
            static_cast<unsigned int>(taskTypeValues.size());
        const unsigned int retained =
            available < maximumTaskTypeValues
                ? available
                : maximumTaskTypeValues;
        for (unsigned int index = 0; index < retained; ++index)
            observation.taskTypeValues.push_back(taskTypeValues[index]);
        observation.taskTypeValuesComplete = retained == available;
        return observation;
    }

    inline const char* RuntimeContextMenuProbeName(
        RuntimeContextMenuProbe probe)
    {
        switch (probe)
        {
        case RUNTIME_CONTEXT_MENU_CAPTURED:
            return "captured";
        case RUNTIME_CONTEXT_MENU_INVALID_TARGET:
            return "invalid_target";
        case RUNTIME_CONTEXT_MENU_CLOSED:
        default:
            return "closed";
        }
    }
}
