#ifndef KENSHI_AGENT_WORLD_TARGET_PROTOCOL_H
#define KENSHI_AGENT_WORLD_TARGET_PROTOCOL_H

#include <string>
#include <vector>

namespace KenshiAgentTelemetry
{
    struct NaturalResourceAssessment
    {
        NaturalResourceAssessment();

        bool structurallyRecognized;
    };

    struct NaturalResourceTargetSnapshot
    {
        NaturalResourceTargetSnapshot();

        std::string id;
        std::string name;
        double positionX;
        double positionY;
        double positionZ;
        double distance;
        double miningResourceLevel;
    };

    NaturalResourceAssessment AssessNaturalResource(
        bool candidateValid,
        bool isMine,
        bool isNaturalMine,
        bool defaultTaskOperatesMachinery);

    std::vector<NaturalResourceTargetSnapshot>
        SelectNearestNaturalResourceTargets(
            const std::vector<NaturalResourceTargetSnapshot>& candidates,
            unsigned int maximumResults);

    bool IsWorldTargetScanAtCapacity(
        unsigned int resultCount,
        unsigned int maximumResults);

    std::string SerializeNaturalResourceTarget(
        const NaturalResourceTargetSnapshot& target);
}

#endif
