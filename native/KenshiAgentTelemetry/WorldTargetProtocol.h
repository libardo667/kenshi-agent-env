#ifndef KENSHI_AGENT_WORLD_TARGET_PROTOCOL_H
#define KENSHI_AGENT_WORLD_TARGET_PROTOCOL_H

#include <ostream>
#include <string>
#include <vector>

namespace KenshiAgentTelemetry
{
    // How Kenshi was asked. The answers are not equally trustworthy, and a list
    // that mixes them without saying which is which is a list nobody can debug
    // -- three separate calls were each mistaken for "what applies here" before
    // provenance existed to tell them apart.
    //
    //   menu - Kenshi's own context-menu builder produced this order for this
    //          target. Not a proxy for the answer, the answer: it is exactly
    //          what a player sees when they right-click.
    //   odds - `getPlayerTaskProbability` returned a success chance above zero.
    //          Sound when it fires, but silent for every order Kenshi renders
    //          no percentage beside, so absence here means nothing at all.
    namespace AdvertisedTaskSource
    {
        extern const char* const MENU;
        extern const char* const ODDS;
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

    // Union by task value, with `menu` outranking `odds`.
    //
    // The two probes overlap and disagree in both directions, so neither can be
    // subtracted from the other. Reporting the union keeps every order Kenshi
    // admitted to, and keeping the stronger provenance means a target that the
    // menu vouched for never gets recorded under the weaker evidence.
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
