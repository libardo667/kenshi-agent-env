#ifndef KENSHI_AGENT_NATIVE_COMMAND_PROTOCOL_H
#define KENSHI_AGENT_NATIVE_COMMAND_PROTOCOL_H

#include <string>

namespace KenshiAgentTelemetry
{
    struct NativeCommandRequest
    {
        NativeCommandRequest();

        std::string commandId;
        std::string command;
        std::string controlMode;
        std::string identitySessionId;
        unsigned long long basedOnTelemetrySequence;
        std::string selectedCharacterId;
        std::string targetId;
        double bearingDegrees;
        double distanceUnits;
    };

    struct NativeCommandAcknowledgement
    {
        NativeCommandAcknowledgement();

        std::string commandId;
        std::string command;
        std::string status;
        std::string reason;
        std::string targetId;
        double bearingDegrees;
        double distanceUnits;
        std::string selectedCharacterId;
        unsigned long long basedOnTelemetrySequence;
        unsigned long long acknowledgedAtTelemetrySequence;
        unsigned long long acceptedAtTelemetrySequence;
        unsigned long long terminalAtTelemetrySequence;
        bool hasAcceptedSequence;
        bool hasTerminalSequence;
    };

    bool IsValidCommandId(const std::string& value);

    bool ParseNativeCommandRequest(
        const std::string& payload,
        NativeCommandRequest& request,
        std::string& rejectionReason);

    std::string SerializeNativeCommandAcknowledgement(
        const NativeCommandAcknowledgement& acknowledgement);
}

#endif
