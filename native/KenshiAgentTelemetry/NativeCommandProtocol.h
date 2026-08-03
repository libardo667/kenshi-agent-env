#ifndef KENSHI_AGENT_NATIVE_COMMAND_PROTOCOL_H
#define KENSHI_AGENT_NATIVE_COMMAND_PROTOCOL_H

#include <string>
#include <vector>

namespace KenshiAgentTelemetry
{
    // A request crosses Python, an atomic file replace, SendInput, and the
    // Kenshi UI-thread hook. At the 500 ms telemetry cadence that proven path
    // advanced by two snapshots in a live run. Four snapshots is the bounded
    // transport allowance; every command still revalidates current selection,
    // target lifetime, role, UI state, and command-specific authority natively.
    const unsigned long long MAX_NATIVE_COMMAND_REVISION_LAG = 4ULL;

    inline bool IsNativeCommandRevisionWithinTransportWindow(
        unsigned long long basedOnTelemetrySequence,
        unsigned long long currentTelemetrySequence)
    {
        return basedOnTelemetrySequence <= currentTelemetrySequence &&
            currentTelemetrySequence - basedOnTelemetrySequence <=
                MAX_NATIVE_COMMAND_REVISION_LAG;
    }

    struct NativeCommandRequest
    {
        NativeCommandRequest();

        std::string commandId;
        std::string command;
        std::string controlMode;
        std::string identitySessionId;
        unsigned long long basedOnTelemetrySequence;
        std::vector<std::string> selectedCharacterIds;
        // Populated only for commands whose selection basis is singular.
        std::string selectedCharacterId;
        std::string targetId;
        std::string contextAction;
        double bearingDegrees;
        double distanceUnits;
        unsigned int minimumOutputQuantity;
    };

    struct NativeCommandAcknowledgement
    {
        NativeCommandAcknowledgement();

        std::string commandId;
        std::string command;
        std::string status;
        std::string reason;
        std::string targetId;
        std::string contextAction;
        double bearingDegrees;
        double distanceUnits;
        unsigned int minimumOutputQuantity;
        std::vector<std::string> selectedCharacterIds;
        // Retained as a compatibility convenience for singular fixtures.
        std::string selectedCharacterId;
        unsigned long long basedOnTelemetrySequence;
        unsigned long long acknowledgedAtTelemetrySequence;
        unsigned long long acceptedAtTelemetrySequence;
        unsigned long long terminalAtTelemetrySequence;
        bool hasAcceptedSequence;
        bool hasTerminalSequence;
    };

    bool IsValidCommandId(const std::string& value);

    std::string FormatStableHandleIdentity(
        unsigned long long processGeneration,
        unsigned long long sessionGeneration,
        unsigned int type,
        unsigned int container,
        unsigned int containerSerial,
        unsigned int index,
        unsigned int serial);

    std::string FormatStableCharacterIdentity(
        unsigned long long processGeneration,
        unsigned long long sessionGeneration,
        unsigned int type,
        unsigned int container,
        unsigned int containerSerial,
        unsigned int index,
        unsigned int serial);

    bool ParseNativeCommandRequest(
        const std::string& payload,
        NativeCommandRequest& request,
        std::string& rejectionReason);

    std::string SerializeNativeCommandAcknowledgement(
        const NativeCommandAcknowledgement& acknowledgement);
}

#endif
