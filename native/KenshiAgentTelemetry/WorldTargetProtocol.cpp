#include "WorldTargetProtocol.h"

#include <iomanip>
#include <locale>
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

    const char* WorldTargetJsonBool(bool value)
    {
        return value ? "true" : "false";
    }
}

namespace KenshiAgentTelemetry
{
    NaturalResourceAssessment::NaturalResourceAssessment()
        : structurallyRecognized(false),
          taskAvailable(false),
          taskProbability(0.0)
    {
    }

    NaturalResourceTargetSnapshot::NaturalResourceTargetSnapshot()
        : positionX(0.0),
          positionY(0.0),
          positionZ(0.0),
          distance(0.0),
          taskAvailable(false),
          taskProbability(0.0),
          miningResourceLevel(0.0)
    {
    }

    NaturalResourceAssessment AssessNaturalResource(
        bool candidateValid,
        bool isNaturalMine,
        bool defaultTaskOperatesMachinery,
        bool currentTaskAvailable,
        double currentTaskProbability)
    {
        NaturalResourceAssessment assessment;
        assessment.structurallyRecognized =
            candidateValid &&
            isNaturalMine &&
            defaultTaskOperatesMachinery;
        if (!assessment.structurallyRecognized)
            return assessment;

        assessment.taskAvailable = currentTaskAvailable;
        assessment.taskProbability = currentTaskProbability;
        return assessment;
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
        json << "\"task_available\":"
             << WorldTargetJsonBool(target.taskAvailable) << ",";
        json << "\"task_probability\":"
             << target.taskProbability << ",";
        json << "\"mining_resource_level\":"
             << target.miningResourceLevel;
        json << "}";
        return json.str();
    }
}
