#ifndef KENSHI_AGENT_WORLD_TARGET_PROTOCOL_H
#define KENSHI_AGENT_WORLD_TARGET_PROTOCOL_H

#include <string>

namespace KenshiAgentTelemetry
{
    struct NaturalResourceAssessment
    {
        NaturalResourceAssessment();

        bool structurallyRecognized;
        bool taskAvailable;
        double taskProbability;
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
        bool taskAvailable;
        double taskProbability;
        double miningResourceLevel;
    };

    NaturalResourceAssessment AssessNaturalResource(
        bool candidateValid,
        bool isNaturalMine,
        bool defaultTaskOperatesMachinery,
        bool currentTaskAvailable,
        double currentTaskProbability);

    bool IsWorldTargetScanAtCapacity(
        unsigned int resultCount,
        unsigned int maximumResults);

    std::string SerializeNaturalResourceTarget(
        const NaturalResourceTargetSnapshot& target);
}

#endif
