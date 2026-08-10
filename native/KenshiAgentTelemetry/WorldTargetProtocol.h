#ifndef KENSHI_AGENT_WORLD_TARGET_PROTOCOL_H
#define KENSHI_AGENT_WORLD_TARGET_PROTOCOL_H

#include <ostream>
#include <string>
#include <vector>

namespace KenshiAgentTelemetry
{
    // How Kenshi was asked. Provenance remains explicit so a future reader
    // cannot silently introduce another proxy for "what applies here".
    //
    //   menu - Kenshi's own context-menu builder produced this order for this
    //          target. Not a proxy for the answer, the answer: it is exactly
    //          what a player sees when they right-click.
    namespace AdvertisedTaskSource
    {
        extern const char* const MENU;
    }

    // One task Kenshi says the current selection may issue to one exact target.
    // Discovered by asking the game, never by a literal in this plug-in.
    //
    // There is deliberately no constructor that omits the source: an answer
    // whose origin has been forgotten is the failure this field exists to
    // prevent, so it must not be expressible.
    struct AdvertisedTask
    {
        AdvertisedTask();
        AdvertisedTask(
            int taskValue,
            const std::string& taskName,
            const std::string& taskSource);

        int value;
        std::string name;
        std::string source;
    };

    // Deduplicate the context menu by task value while preserving its order.
    void MergeAdvertisedTask(
        std::vector<AdvertisedTask>& tasks,
        const AdvertisedTask& task);

    // Probing every target every snapshot is unnecessary: the frontier only
    // needs each (kind, task) pair once, and the agent can only act on what is
    // near it. Nearest targets are probed; the rest report that they were not,
    // so an empty list never reads as "this target affords nothing".
    bool IsWithinTargetProbeBudget(
        unsigned int probedCount,
        unsigned int probeBudget);

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
        bool hasScreenPosition;
        double screenX;
        double screenY;
        bool advertisedTasksProbed;
        std::vector<AdvertisedTask> advertisedTasks;
        bool operatorCapacityKnown;
        int operatorCapacity;
        bool currentOperatorsComplete;
        std::vector<std::string> currentOperatorIds;
        bool outputInventoryComplete;

        struct OutputItem
        {
            OutputItem();

            std::string name;
            int quantity;
            int itemType;
        };

        std::vector<OutputItem> outputInventory;
    };

    // Serialize the discovered vocabulary for one target, including whether it
    // was probed at all.
    void AppendAdvertisedTasks(
        std::ostream& json,
        bool probed,
        const std::vector<AdvertisedTask>& tasks);

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
