#include "WorldTargetProtocol.h"

#include <algorithm>
#include <iomanip>
#include <locale>
#include <set>
#include <sstream>

namespace
{
    std::string WorldTargetJsonEscape(const std::string& value)
    {
        std::ostringstream escaped;
        for (std::string::const_iterator it = value.begin();
             it != value.end();
             ++it)
        {
            const unsigned char character =
                static_cast<unsigned char>(*it);
            switch (character)
            {
            case '"':
                escaped << "\\\"";
                break;
            case '\\':
                escaped << "\\\\";
                break;
            case '\b':
                escaped << "\\b";
                break;
            case '\f':
                escaped << "\\f";
                break;
            case '\n':
                escaped << "\\n";
                break;
            case '\r':
                escaped << "\\r";
                break;
            case '\t':
                escaped << "\\t";
                break;
            default:
                if (character < 0x20)
                {
                    escaped << "\\u"
                            << std::hex
                            << std::setw(4)
                            << std::setfill('0')
                            << static_cast<unsigned int>(character)
                            << std::dec
                            << std::setfill(' ');
                }
                else
                {
                    escaped << static_cast<char>(character);
                }
                break;
            }
        }
        return escaped.str();
    }

    bool NaturalResourceTargetLess(
        const KenshiAgentTelemetry::NaturalResourceTargetSnapshot& left,
        const KenshiAgentTelemetry::NaturalResourceTargetSnapshot& right)
    {
        if (left.distance != right.distance)
            return left.distance < right.distance;
        if (left.name != right.name)
            return left.name < right.name;
        return left.id < right.id;
    }
}

namespace KenshiAgentTelemetry
{
    NaturalResourceAssessment::NaturalResourceAssessment()
        : structurallyRecognized(false)
    {
    }

    AdvertisedTask::AdvertisedTask()
        : value(0)
    {
    }

    namespace AdvertisedTaskSource
    {
        const char* const MENU = "menu";
    }

    AdvertisedTask::AdvertisedTask(
        int taskValue,
        const std::string& taskName,
        const std::string& taskSource)
        : value(taskValue),
          name(taskName),
          source(taskSource)
    {
    }

    void MergeAdvertisedTask(
        std::vector<AdvertisedTask>& tasks,
        const AdvertisedTask& task)
    {
        for (std::vector<AdvertisedTask>::iterator it = tasks.begin();
             it != tasks.end();
             ++it)
        {
            if (it->value != task.value)
                continue;
            return;
        }
        tasks.push_back(task);
    }

    bool IsWithinTargetProbeBudget(
        unsigned int probedCount,
        unsigned int probeBudget)
    {
        return probedCount < probeBudget;
    }

    void AppendAdvertisedTasks(
        std::ostream& json,
        bool probed,
        const std::vector<AdvertisedTask>& tasks)
    {
        json << "\"advertised_tasks_probed\":"
             << (probed ? "true" : "false") << ",";
        json << "\"advertised_tasks\":[";
        for (std::vector<AdvertisedTask>::const_iterator it = tasks.begin();
             it != tasks.end();
             ++it)
        {
            if (it != tasks.begin())
                json << ",";
            json << "{\"value\":" << it->value
                 << ",\"name\":\"" << WorldTargetJsonEscape(it->name)
                 << "\",\"source\":\"" << WorldTargetJsonEscape(it->source)
                 << "\"}";
        }
        json << "]";
    }

    NaturalResourceTargetSnapshot::NaturalResourceTargetSnapshot()
        : positionX(0.0),
          positionY(0.0),
          positionZ(0.0),
          distance(0.0),
          miningResourceLevel(0.0),
          hasScreenPosition(false),
          screenX(0.0),
          screenY(0.0),
          advertisedTasksProbed(false),
          operatorCapacityKnown(false),
          operatorCapacity(0),
          currentOperatorsComplete(false),
          outputInventoryComplete(false)
    {
    }

    NaturalResourceTargetSnapshot::OutputItem::OutputItem()
        : quantity(0),
          itemType(0)
    {
    }

    NaturalResourceAssessment AssessNaturalResource(
        bool candidateValid,
        bool isMine,
        bool isNaturalMine,
        bool defaultTaskOperatesMachinery)
    {
        NaturalResourceAssessment assessment;
        assessment.structurallyRecognized =
            candidateValid &&
            (isMine || isNaturalMine) &&
            defaultTaskOperatesMachinery;
        return assessment;
    }

    std::vector<NaturalResourceTargetSnapshot>
        SelectNearestNaturalResourceTargets(
            const std::vector<NaturalResourceTargetSnapshot>& candidates,
            unsigned int maximumResults)
    {
        std::vector<NaturalResourceTargetSnapshot> ordered(candidates);
        std::sort(
            ordered.begin(),
            ordered.end(),
            NaturalResourceTargetLess);

        std::vector<NaturalResourceTargetSnapshot> selected;
        std::set<std::string> seenIds;
        for (std::vector<NaturalResourceTargetSnapshot>::const_iterator it =
                 ordered.begin();
             it != ordered.end() &&
                 selected.size() < maximumResults;
             ++it)
        {
            if (it->id.empty() || !seenIds.insert(it->id).second)
                continue;
            selected.push_back(*it);
        }
        return selected;
    }

    bool IsWorldTargetScanAtCapacity(
        unsigned int resultCount,
        unsigned int maximumResults)
    {
        return maximumResults > 0 && resultCount >= maximumResults;
    }

    std::string SerializeNaturalResourceTarget(
        const NaturalResourceTargetSnapshot& target)
    {
        std::ostringstream json;
        json.imbue(std::locale::classic());
        json << std::setprecision(7);
        json << "{";
        json << "\"id\":\""
             << WorldTargetJsonEscape(target.id) << "\",";
        json << "\"name\":\""
             << WorldTargetJsonEscape(target.name) << "\",";
        json << "\"kind\":\"natural_resource\",";
        json << "\"position\":{";
        json << "\"x\":" << target.positionX << ",";
        json << "\"y\":" << target.positionY << ",";
        json << "\"z\":" << target.positionZ;
        json << "},";
        json << "\"distance\":" << target.distance << ",";
        json << "\"context_actions\":[\"operate\"],";
        json << "\"default_task\":\"operate_machinery\",";
        AppendAdvertisedTasks(
            json,
            target.advertisedTasksProbed,
            target.advertisedTasks);
        json << ",\"mining_resource_level\":"
             << target.miningResourceLevel;
        json << ",\"operator_capacity\":";
        if (target.operatorCapacityKnown)
            json << target.operatorCapacity;
        else
            json << "null";
        json << ",\"current_operator_ids\":[";
        for (std::vector<std::string>::const_iterator it =
                 target.currentOperatorIds.begin();
             it != target.currentOperatorIds.end();
             ++it)
        {
            if (it != target.currentOperatorIds.begin())
                json << ",";
            json << "\"" << WorldTargetJsonEscape(*it) << "\"";
        }
        json << "],\"current_operators_complete\":"
             << (target.currentOperatorsComplete ? "true" : "false");
        json << ",\"output_inventory\":[";
        for (std::vector<NaturalResourceTargetSnapshot::OutputItem>::
                 const_iterator it = target.outputInventory.begin();
             it != target.outputInventory.end();
             ++it)
        {
            if (it != target.outputInventory.begin())
                json << ",";
            json << "{\"name\":\"" << WorldTargetJsonEscape(it->name)
                 << "\",\"quantity\":" << it->quantity
                 << ",\"item_type\":" << it->itemType << "}";
        }
        json << "],\"output_inventory_complete\":"
             << (target.outputInventoryComplete ? "true" : "false");
        if (target.hasScreenPosition)
        {
            json << ",\"screen_position\":{";
            json << "\"x\":" << target.screenX << ",";
            json << "\"y\":" << target.screenY;
            json << "}";
        }
        json << "}";
        return json.str();
    }
}
