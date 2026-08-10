#ifndef KENSHI_AGENT_NATIVE_COMMAND_PROTOCOL_H
#define KENSHI_AGENT_NATIVE_COMMAND_PROTOCOL_H

#include <map>
#include <string>
#include <vector>

namespace KenshiAgentTelemetry
{
    // A request crosses Python, an atomic file replace, and the Kenshi UI-thread
    // hook. The plug-in notices the file replacement itself; no keyboard or
    // pointer trigger is part of dispatch. At the 500 ms telemetry cadence the
    // proven transport advanced by two snapshots in a live run.
    // Four snapshots is the bounded allowance; every command still revalidates
    // current selection, target lifetime, role, UI state, and command-specific
    // authority natively.
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
        // source owner; these are the rest of the address. Native code resolves
        // the item through `InventorySection::getItemAt(x, y)`.
        std::string destinationId;
        std::string sectionName;
        // Title-screen addresses are their own domains. SaveManager accepts an
        // exact save name or an exact Game Start ID; neither is an entity.
        std::string saveName;
        std::string gameStartId;
        int slotX;
        int slotY;
        // Exact ordered reply identity. Index alone is unsafe because the
        // conversation can advance between publication and dispatch.
        int dialogueOptionIndex;
        std::string dialogueOptionText;
        // Time control, which Kenshi owns directly: `GameWorld::userPause` and
        // `GameWorld::setGameSpeed`. These were the last two operations reaching
        // the game through a keystroke, and a keystroke is why setting a faster
        // gear could not start a paused world -- the speed keys select a rate
        // without resuming, so the controller had to press twice.
        bool pauseRequested;
        double speedMultiplier;
        // How many of a stack to move. Zero means the whole stack, which is
        // what a transfer always silently did: one buy took every Meatwrap the
        // shop had, and nothing in the request said so.
        int quantity;
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
        // source owner; these are the rest of the address. Native code resolves
        // the item through `InventorySection::getItemAt(x, y)`.
        std::string destinationId;
        std::string sectionName;
        std::string saveName;
        std::string gameStartId;
        int slotX;
        int slotY;
        int dialogueOptionIndex;
        std::string dialogueOptionText;
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
    bool NativeCommandDrivesTitleScreen(const std::string& command);
    bool NativeCommandClosesWindows(const std::string& command);

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

    class StableCharacterIdentityRegistry
    {
    public:
        std::string Resolve(
            unsigned long long objectAddress,
            int validKey,
            const std::string& candidateId);
        void Clear();

    private:
        struct Entry
        {
            Entry();
            Entry(int key, const std::string& value);

            int validKey;
            std::string id;
        };

        std::map<unsigned long long, Entry> entries_;
    };

    bool ParseNativeCommandRequest(
        const std::string& payload,
        NativeCommandRequest& request,
        std::string& rejectionReason);

    std::string SerializeNativeCommandAcknowledgement(
        const NativeCommandAcknowledgement& acknowledgement);
}

#endif
