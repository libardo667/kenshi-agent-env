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
        // A transfer names two inventories and one slot. `targetId` is the
        // source owner; these are the rest of the address. Kenshi's own
        // `RClickAutoTrade` takes a section name and an x/y, so a slot is how
        // an item is named to the engine.
        std::string destinationId;
        std::string sectionName;
        int slotX;
        int slotY;
        // Time control, which Kenshi owns directly: `GameWorld::userPause` and
        // `GameWorld::setGameSpeed`. These were the last two operations reaching
        // the game through a keystroke, and a keystroke is why setting a faster
        // gear could not start a paused world -- the speed keys select a rate
        // without resuming, so the controller had to press twice.
        bool pauseRequested;
        double speedMultiplier;
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
        // A transfer names two inventories and one slot. `targetId` is the
        // source owner; these are the rest of the address. Kenshi's own
        // `RClickAutoTrade` takes a section name and an x/y, so a slot is how
        // an item is named to the engine.
        std::string destinationId;
        std::string sectionName;
        int slotX;
        int slotY;
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

    // The one native command vocabulary, and the one place a command's
    // wire shape is classified.
    //
    // Both were duplicated: the parser and the dispatcher each carried their
    // own list of accepted names, and a Python copy of the same vocabulary
    // lived in three more places. Adding a command meant editing five lists,
    // and any miss failed somewhere distant from the edit.
    //
    // These answer wire *shape* only - whether a command names a target or
    // carries a direction. Recipient scope is the operation registry's, never
    // decided from a command name here.
    bool IsKnownNativeCommand(const std::string& command);
    bool NativeCommandNamesTarget(const std::string& command);
    bool NativeCommandCarriesDirection(const std::string& command);
    bool NativeCommandControlsTime(const std::string& command);

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
